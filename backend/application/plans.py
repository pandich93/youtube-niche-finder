"""Tariffs (SaaS sub-project 1): which plan a user is on and what it allows.

plan_for(user_id) is the one answer every check goes through. No row in
user_plans = the base plan; an expired row = the base plan at once (the
worker's expire_due() records it later). Admins and the system user are not
limited; single-user mode gets the catalog defaults, exactly as before plans
existed. Nothing here deletes data: a smaller plan only stops new rows.
"""
import logging
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import infrastructure.postgres as db
from domain import plans as DP
from domain.i18n import tr
from domain.users import SYSTEM_USER_ID, multi_user_enabled
from infrastructure import quota_owner

_log = logging.getLogger("niche-finder.plans")

# count limit -> the personal table whose rows it counts
COUNT_TABLES = {"own_channels": "own_channels", "tracked_channels": "tracked_channels",
                "topics": "user_topics", "saved_items": "saved_items", "drafts": "drafts",
                "api_tokens": "api_tokens"}


class PlanError(ValueError):
    """Bad input to a plan operation; the HTTP layer answers 400."""


@dataclass(frozen=True)
class Entitlements:
    plan_id: int | None
    name: str
    limits: dict
    features: dict
    expires_at: str | None = None
    unlimited: bool = False


def _defaults() -> Entitlements:
    return Entitlements(None, "Free", DP.resolve_limits({}), DP.resolve_features({}))


def _unlimited() -> Entitlements:
    return Entitlements(None, "Без ограничений", {k: None for k in DP.LIMITS},
                        {k: True for k in DP.FEATURES}, unlimited=True)


def _obj(v):
    """A stored limits/features value; anything but an object is kept as an
    invalid marker so validate() reports it and the env fallback applies."""
    return dict(v or {}) if isinstance(v or {}, dict) else {"__invalid__": True}


def _shape(r) -> dict:
    return {"id": r["id"], "slug": r["slug"], "name": r["name"], "description": r["description"],
            "price": float(r["price"] or 0), "currency": r["currency"],
            "periodDays": r["period_days"], "isBase": bool(r["is_base"]),
            "isPublic": bool(r["is_public"]), "archived": r["archived_at"] is not None,
            "sortOrder": r["sort_order"], "limits": _obj(r["limits"]), "features": _obj(r["features"])}


def _usable(plan) -> bool:
    return plan is not None and not DP.validate(plan["limits"], plan["features"])


def plan_for(user_id) -> Entitlements:
    if not multi_user_enabled():
        return _defaults()
    if user_id is None or user_id == SYSTEM_USER_ID:
        return _unlimited()
    conn = db.get_conn()
    try:
        u = conn.execute("SELECT u.is_admin, up.plan_id, up.expires_at FROM users u "
                         "LEFT JOIN user_plans up ON up.user_id = u.id WHERE u.id = ?",
                         (user_id,)).fetchone()
        if u and u["is_admin"]:
            return _unlimited()
        base = conn.execute("SELECT * FROM plans WHERE is_base = 1").fetchone()
        own = (conn.execute("SELECT * FROM plans WHERE id = ?", (u["plan_id"],)).fetchone()
               if u and u["plan_id"] else None)
    finally:
        conn.close()
    base = _shape(base) if base else None
    own = _shape(own) if own else None
    if own and _usable(own) and not DP.expired(u["expires_at"], datetime.now(timezone.utc)):
        return Entitlements(own["id"], own["name"], DP.resolve_limits(own["limits"]),
                            DP.resolve_features(own["features"]), u["expires_at"])
    if not _usable(base):
        _log.warning("no usable base plan -- using the .env defaults")
        return _defaults()
    return Entitlements(base["id"], base["name"], DP.resolve_limits(base["limits"]),
                        DP.resolve_features(base["features"]))


def _yt_limits(user_id) -> dict:
    lim = plan_for(user_id).limits
    return {"units": lim["yt_units"], "searchCalls": lim["search_calls"]}


# The YouTube client (infrastructure) reads a user's daily budget through this.
quota_owner.set_limits_provider(_yt_limits)


def _count(conn, key, user_id) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {COUNT_TABLES[key]} WHERE user_id = ?",
                        (user_id,)).fetchone()[0]


def ensure_room(user_id, key: str) -> None:
    limit = plan_for(user_id).limits[key]
    if limit is None:
        return
    conn = db.get_conn()
    try:
        n = _count(conn, key, user_id)
    finally:
        conn.close()
    if not DP.has_room(limit, n):
        raise DP.PlanLimitReached(key, int(limit))


def ensure_feature(user_id, key: str) -> None:
    if not plan_for(user_id).features[key]:
        raise DP.FeatureOff(key)


def usage(user_id) -> dict:
    import infrastructure.youtube.client as yt
    today = datetime.now(timezone.utc).date().isoformat()
    conn = db.get_conn()
    try:
        llm = conn.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM llm_usage "
                           "WHERE day = ? AND user_id = ?", (today, user_id)).fetchone()[0]
        return {"daily": {"yt_units": int(db.get_meta(conn, yt.user_units_key(user_id)) or 0),
                          "search_calls": int(db.get_meta(conn, yt.user_search_key(user_id)) or 0),
                          "llm_usd": round(float(llm or 0), 4)},
                "counts": {key: _count(conn, key, user_id) for key in COUNT_TABLES}}
    finally:
        conn.close()


def summary(user_id) -> dict:
    ent = plan_for(user_id)
    # an admin-made plan's name is data; only the built-in "unlimited" one is ours to translate
    name = tr(ent.name) if ent.unlimited else ent.name
    return {"plan": {"id": ent.plan_id, "name": name}, "expiresAt": ent.expires_at,
            "unlimited": ent.unlimited, "limits": ent.limits, "features": ent.features,
            "usage": usage(user_id)}


def catalog() -> dict:
    """Limit and feature labels (and units) in the reader's language."""
    return {"limits": [{"key": k, "label": tr(s["label"]), "unit": tr(s["unit"]), "kind": s["kind"],
                        "default": s["default"]()} for k, s in DP.LIMITS.items()],
            "features": [{"key": k, "label": tr(label)} for k, label in DP.FEATURES.items()]}


def list_plans() -> list:
    conn = db.get_conn()
    try:
        rows = conn.execute("SELECT * FROM plans ORDER BY sort_order, id").fetchall()
        counts = {r["plan_id"]: r["n"] for r in conn.execute(
            "SELECT plan_id, COUNT(*) AS n FROM user_plans GROUP BY plan_id").fetchall()}
        no_row = conn.execute("SELECT COUNT(*) FROM users u WHERE NOT EXISTS "
                              "(SELECT 1 FROM user_plans up WHERE up.user_id = u.id)").fetchone()[0]
    finally:
        conn.close()
    out = []
    for r in rows:
        p = _shape(r)
        p["effectiveLimits"] = DP.resolve_limits(p["limits"])
        p["effectiveFeatures"] = DP.resolve_features(p["features"])
        p["users"] = counts.get(p["id"], 0) + (no_row if p["isBase"] else 0)
        out.append(p)
    return out


def public_plans() -> list:
    return [p for p in list_plans() if p["isPublic"] and not p["archived"]]


def _log_change(conn, user_id, from_id, to_id, expires_at, by, now):
    conn.execute("INSERT INTO plan_changes (user_id, from_plan_id, to_plan_id, expires_at, "
                 "changed_by, changed_at) VALUES (?,?,?,?,?,?)",
                 (user_id, from_id, to_id, expires_at, by, now.isoformat()))


def _set_plan(conn, user_id, plan_id, expires_at, by, now):
    prev = conn.execute("SELECT plan_id FROM user_plans WHERE user_id = ?", (user_id,)).fetchone()
    conn.execute("INSERT INTO user_plans (user_id, plan_id, started_at, expires_at, assigned_by) "
                 "VALUES (?,?,?,?,?) ON CONFLICT (user_id) DO UPDATE SET plan_id = EXCLUDED.plan_id, "
                 "started_at = EXCLUDED.started_at, expires_at = EXCLUDED.expires_at, "
                 "assigned_by = EXCLUDED.assigned_by",
                 (user_id, plan_id, now.isoformat(), expires_at, by))
    _log_change(conn, user_id, prev["plan_id"] if prev else None, plan_id, expires_at, by, now)


def choose(user_id, plan_id) -> None:
    now = datetime.now(timezone.utc)
    conn = db.get_conn()
    try:
        p = conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
        if not p or p["archived_at"] or not p["is_public"]:
            raise PlanError(tr("такого тарифа нет"))
        if float(p["price"] or 0) > 0:
            raise PlanError(tr("оплата пока не подключена -- этот тариф назначает администратор"))
        expires = ((now + timedelta(days=p["period_days"])).isoformat()
                   if p["period_days"] and not p["is_base"] else None)
        _set_plan(conn, user_id, p["id"], expires, "self", now)
        conn.commit()
    finally:
        conn.close()


def assign(admin_id, user_id, plan_id, expires_at) -> None:
    now = datetime.now(timezone.utc)
    if expires_at:
        try:
            datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
        except ValueError:
            raise PlanError(tr("expiresAt: нужна дата в формате ISO")) from None
    conn = db.get_conn()
    try:
        if not conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone():
            raise PlanError(tr("нет такого пользователя"))
        p = conn.execute("SELECT id, archived_at, is_base FROM plans WHERE id = ?",
                         (plan_id,)).fetchone()
        if not p or p["archived_at"]:
            raise PlanError(tr("такого тарифа нет"))
        _set_plan(conn, user_id, p["id"], None if p["is_base"] else (expires_at or None),
                  f"admin:{admin_id}", now)
        conn.commit()
    finally:
        conn.close()


def expire_due(now: datetime = None) -> int:
    """The worker's daily step: rows past their date move to the base plan."""
    now = now or datetime.now(timezone.utc)
    conn = db.get_conn()
    try:
        base = conn.execute("SELECT id FROM plans WHERE is_base = 1").fetchone()
        if not base:
            return 0
        n = 0
        for r in conn.execute("SELECT user_id, plan_id, expires_at FROM user_plans "
                              "WHERE expires_at IS NOT NULL").fetchall():
            if not DP.expired(r["expires_at"], now):
                continue
            conn.execute("UPDATE user_plans SET plan_id = ?, started_at = ?, expires_at = NULL, "
                         "assigned_by = 'system' WHERE user_id = ?",
                         (base["id"], now.isoformat(), r["user_id"]))
            _log_change(conn, r["user_id"], r["plan_id"], base["id"], None, "system", now)
            n += 1
        conn.commit()
        return n
    finally:
        conn.close()


_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,40}")


def _clean(data: dict) -> dict:
    data = data or {}
    errors = []
    name = str(data.get("name") or "").strip()
    if not name:
        errors.append(tr("name: обязательно"))
    slug = str(data.get("slug") or "").strip().lower()
    if not _SLUG.fullmatch(slug):
        errors.append(tr("slug: латиница в нижнем регистре, цифры и дефис"))
    try:
        price = round(float(data.get("price") or 0), 2)
        if not math.isfinite(price) or price < 0 or price >= 100_000_000:
            raise ValueError
    except (TypeError, ValueError):
        price = 0
        errors.append(tr("price: число не меньше 0"))
    currency = str(data.get("currency") or "USD").strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", currency):
        errors.append(tr("currency: три латинские буквы, например USD"))
    period = data.get("periodDays")
    if period in ("", None):
        period = None
    else:
        try:
            period = int(period)
            if period < 1:
                raise ValueError
        except (TypeError, ValueError):
            errors.append(tr("periodDays: целое число дней от 1 или пусто"))
    is_base, is_public = bool(data.get("isBase")), bool(data.get("isPublic", True))
    if is_base and price:
        errors.append(tr("price: базовый тариф бесплатный"))
    if is_base and period:
        errors.append(tr("periodDays: у базового тарифа нет срока"))
    if is_base and not is_public:
        errors.append(tr("isPublic: базовый тариф всегда виден"))
    try:
        sort_order = int(data.get("sortOrder") or 0)
    except (TypeError, ValueError):
        sort_order = 0
        errors.append(tr("sortOrder: целое число"))
    limits, features = data.get("limits") or {}, data.get("features") or {}
    errors += DP.validate(limits, features)
    if errors:
        raise PlanError("; ".join(errors))
    return {"slug": slug, "name": name, "description": str(data.get("description") or "").strip(),
            "price": price, "currency": currency, "period_days": period, "is_base": 1 if is_base else 0,
            "is_public": 1 if is_public else 0, "sort_order": sort_order,
            "limits": limits, "features": features}


def _write(conn, plan_id, c, now):
    import json
    if c["is_base"]:
        if plan_id is None:
            conn.execute("UPDATE plans SET is_base = 0 WHERE is_base = 1")
        else:
            conn.execute("UPDATE plans SET is_base = 0 WHERE is_base = 1 AND id != ?", (plan_id,))
    params = (c["slug"], c["name"], c["description"], c["price"], c["currency"], c["period_days"],
              c["is_base"], c["is_public"], c["sort_order"], json.dumps(c["limits"]),
              json.dumps(c["features"]), now)
    if plan_id is None:
        return conn.execute(
            "INSERT INTO plans (slug, name, description, price, currency, period_days, is_base, "
            "is_public, sort_order, limits, features, updated_at, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?::jsonb,?::jsonb,?,?) RETURNING *",
            params + (now,)).fetchone()
    return conn.execute(
        "UPDATE plans SET slug = ?, name = ?, description = ?, price = ?, currency = ?, "
        "period_days = ?, is_base = ?, is_public = ?, sort_order = ?, limits = ?::jsonb, "
        "features = ?::jsonb, updated_at = ? WHERE id = ? RETURNING *", params + (plan_id,)).fetchone()


def create_plan(data: dict) -> dict:
    c = _clean(data)
    conn = db.get_conn()
    try:
        if conn.execute("SELECT 1 FROM plans WHERE slug = ?", (c["slug"],)).fetchone():
            raise PlanError(tr("slug: тариф {slug} уже есть", slug=c["slug"]))
        row = _write(conn, None, c, db.now_iso())
        conn.commit()
        return _shape(row)
    finally:
        conn.close()


def update_plan(plan_id, data: dict) -> dict:
    c = _clean(data)
    conn = db.get_conn()
    try:
        cur = conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
        if not cur:
            raise PlanError(tr("такого тарифа нет"))
        if cur["is_base"] and not c["is_base"]:
            raise PlanError(tr("isBase: сначала назначьте базовым другой тариф"))
        if c["is_base"] and cur["archived_at"]:
            raise PlanError(tr("isBase: архивный тариф не может быть базовым"))
        if conn.execute("SELECT 1 FROM plans WHERE slug = ? AND id != ?",
                        (c["slug"], plan_id)).fetchone():
            raise PlanError(tr("slug: тариф {slug} уже есть", slug=c["slug"]))
        row = _write(conn, plan_id, c, db.now_iso())
        conn.commit()
        return _shape(row)
    finally:
        conn.close()


def set_archived(plan_id, archived: bool) -> dict:
    conn = db.get_conn()
    try:
        cur = conn.execute("SELECT is_base FROM plans WHERE id = ?", (plan_id,)).fetchone()
        if not cur:
            raise PlanError(tr("такого тарифа нет"))
        if archived and cur["is_base"]:
            raise PlanError(tr("базовый тариф нельзя архивировать"))
        row = conn.execute("UPDATE plans SET archived_at = ?, updated_at = ? WHERE id = ? RETURNING *",
                           (db.now_iso() if archived else None, db.now_iso(), plan_id)).fetchone()
        conn.commit()
        return _shape(row)
    finally:
        conn.close()


def list_users() -> list:
    conn = db.get_conn()
    try:
        rows = conn.execute("SELECT id, email, is_admin, created_at FROM users ORDER BY id").fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        ent = plan_for(r["id"])
        daily = usage(r["id"])["daily"]
        out.append({"id": r["id"], "email": r["email"], "isAdmin": bool(r["is_admin"]),
                    "createdAt": r["created_at"],
                    "plan": {"id": ent.plan_id, "name": ent.name}, "expiresAt": ent.expires_at,
                    "today": {"ytUnits": daily["yt_units"], "llmUsd": daily["llm_usd"]}})
    return out
