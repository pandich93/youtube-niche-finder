"""Счётчик ручных запусков пользователя (многопользовательский план P2-11).

Каждый пропущенный запуск -- строка manual_runs (user_id, operation,
started_at); строки старше часа удаляются при следующей проверке. В Postgres,
а не в памяти процесса: переживает перезапуск и одинаков для нескольких
реплик web. Проверка и запись -- в одной транзакции под advisory-блокировкой
на (операция, пользователь), как ensure_room тарифов (P1-10): два запроса
сразу не увидят оба свободное место под последний запуск.

Таблица личная (PERSONAL_TABLES, Row-Level Security): маршрут пишет её в
области своего пользователя, user_id -- явно в каждом запросе.

Расход квоты YouTube и LLM запуска здесь не считается: его, как и раньше,
списывает на пользователя запроса quota_owner (session_guard).
"""
from datetime import datetime, timezone

import infrastructure.postgres as db
from application import plans as PL
from domain import manual_runs as DM
from domain import plans as PD


def _iso(ts) -> str:
    # всегда с микросекундами: строки сравниваются в SQL как текст
    return ts.isoformat(timespec="microseconds")


def _parse(value):
    ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def per_hour(*, user_id):
    """Лимит тарифа: запусков в час на операцию; 0 -- только администратор,
    None -- без лимита."""
    return PL.plan_for(user_id).limits["manual_runs"]


def _starts(conn, user_id, operation):
    return [_parse(r["started_at"]) for r in conn.execute(
        "SELECT started_at FROM manual_runs WHERE user_id = ? AND operation = ?",
        (user_id, operation)).fetchall()]


def start(operation: str, *, user_id, now=None):
    """Занять запуск `operation` для user_id. Id строки (отдать её назад --
    release, если запуск не удался), None -- без лимита, учитывать нечего.
    AdminOnly -- лимит 0, ManualRunLimited -- за этот час всё истрачено."""
    if operation not in DM.OPERATIONS:
        raise ValueError(f"unknown manual operation {operation!r}")
    limit = per_hour(user_id=user_id)
    if limit == 0:
        raise PD.AdminOnly()
    if limit is None:
        return None
    now = now or datetime.now(timezone.utc)
    conn = db.get_conn()
    try:
        db.lock_until_commit(conn, f"manual_run:{operation}", user_id)
        conn.execute("DELETE FROM manual_runs WHERE user_id = ? AND operation = ? "
                     "AND started_at <= ?", (user_id, operation, _iso(now - DM.WINDOW)))
        at = DM.next_at(_starts(conn, user_id, operation), limit, now)
        if at is not None:
            conn.commit()                 # старые строки убраны, блокировка отпущена
            raise DM.ManualRunLimited(operation, int(limit), at, now)
        row = conn.execute("INSERT INTO manual_runs (user_id, operation, started_at) "
                           "VALUES (?, ?, ?) RETURNING id",
                           (user_id, operation, _iso(now))).fetchone()
        conn.commit()
        return row["id"]
    finally:
        conn.close()


def release(run_id, *, user_id):
    """Запуск упал (нет квоты, ошибка YouTube) -- он не в счёт."""
    if run_id is None:
        return
    conn = db.get_conn()
    try:
        conn.execute("DELETE FROM manual_runs WHERE id = ? AND user_id = ?", (run_id, user_id))
        conn.commit()
    finally:
        conn.close()


def status(*, user_id, is_admin: bool, now=None) -> dict:
    """Что дашборд показывает у кнопок: можно ли запускать и когда снова."""
    ops = {}
    if is_admin:
        return {"admin": True, "perHour": None,
                "operations": {op: {"allowed": True, "nextAt": None} for op in DM.OPERATIONS}}
    limit = per_hour(user_id=user_id)
    now = now or datetime.now(timezone.utc)
    if limit:
        conn = db.get_conn()
        try:
            starts = {op: _starts(conn, user_id, op) for op in DM.OPERATIONS}
        finally:
            conn.close()
    for op in DM.OPERATIONS:
        at = DM.next_at(starts[op], limit, now) if limit else None
        ops[op] = {"allowed": limit != 0 and at is None, "nextAt": _iso(at) if at else None}
    return {"admin": False, "perHour": limit, "operations": ops}
