"""Swipe file -- save a video or channel you noticed, with a snapshot of the
metrics it had at the time (plan item 8.6).

Deliberately dumb: this module does not re-fetch anything and spends zero
YouTube quota. `payload` is whatever the caller already computed (typically
the same dict inspection.inspect_video/inspect_channel just returned) --
we store it as-is so that months later you can see what the video looked
like when you noticed it, not just what it looks like now.
"""
import json

import infrastructure.postgres as db
from domain.users import LOCAL_USER_ID

VALID_KINDS = ("video", "channel")
DEFAULT_FOLDER = "default"


def save_item(kind: str, ref_id: str, payload: dict = None, note: str = None,
              folder: str = None, user_id: int = LOCAL_USER_ID) -> dict:
    if kind not in VALID_KINDS:
        raise ValueError(f"kind must be one of {VALID_KINDS}, got {kind!r}")
    if not ref_id:
        raise ValueError("ref_id is required")
    conn = db.get_conn()
    now = db.now_iso()
    row = conn.execute(
        "INSERT INTO saved_items (user_id, kind, ref_id, folder, note, payload, created_at) "
        "VALUES (?,?,?,?,?,?,?) RETURNING id",
        (user_id, kind, ref_id, folder or DEFAULT_FOLDER, note,
         json.dumps(payload, ensure_ascii=False) if payload else None, now),
    ).fetchone()
    conn.commit()
    conn.close()
    return {"id": row["id"], "kind": kind, "refId": ref_id, "folder": folder or DEFAULT_FOLDER,
           "note": note, "createdAt": now}


def _shape(r) -> dict:
    payload = None
    if r["payload"]:
        try:
            payload = json.loads(r["payload"])
        except (TypeError, ValueError):
            payload = None
    return {
        "id": r["id"], "kind": r["kind"], "refId": r["ref_id"], "folder": r["folder"],
        "note": r["note"], "payload": payload, "createdAt": r["created_at"],
    }


def list_items(kind: str = None, folder: str = None, limit: int = 200,
               user_id: int = LOCAL_USER_ID) -> list:
    where, params = ["user_id = ?"], [user_id]
    if kind:
        where.append("kind = ?")
        params.append(kind)
    if folder:
        where.append("folder = ?")
        params.append(folder)
    sql = "SELECT * FROM saved_items"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    conn = db.get_conn()
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [_shape(r) for r in rows]


def list_folders(user_id: int = LOCAL_USER_ID) -> list:
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT folder, COUNT(*) AS n FROM saved_items WHERE user_id = ? GROUP BY folder "
        "ORDER BY folder", (user_id,)).fetchall()
    conn.close()
    return [{"folder": r["folder"], "count": r["n"]} for r in rows]


def delete_item(item_id: int, user_id: int = LOCAL_USER_ID) -> dict:
    """Only the owner's item goes; someone else's id deletes nothing
    (deleted: None)."""
    conn = db.get_conn()
    row = conn.execute("DELETE FROM saved_items WHERE id = ? AND user_id = ? RETURNING id",
                       (item_id, user_id)).fetchone()
    conn.commit()
    conn.close()
    return {"deleted": row["id"] if row else None}


def is_saved(kind: str, ref_id: str, user_id: int = LOCAL_USER_ID) -> bool:
    conn = db.get_conn()
    row = conn.execute(
        "SELECT 1 FROM saved_items WHERE kind=? AND ref_id=? AND user_id=? LIMIT 1",
        (kind, ref_id, user_id)).fetchone()
    conn.close()
    return bool(row)
