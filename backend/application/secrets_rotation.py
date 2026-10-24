"""Every encrypted value at rest, in one place (P1-12): how many are on the
current key, how many still need an old one, how many no key opens -- and
re-encrypting the old ones with the current key (`cli.py rotate-secrets`).

The plaintext never leaves infrastructure/secrets.py: this module only moves
ciphertext and counts. A row that changed under the rotation (the user saved
a new token meanwhile) is left alone -- the UPDATE matches the old ciphertext.
"""
import logging

import infrastructure.postgres as db
import infrastructure.secrets as SEC

log = logging.getLogger(__name__)

# (table, encrypted column, key columns) -- everything SEC.encrypt() writes
FIELDS = (
    ("own_channels", "token_enc", ("user_id", "channel_id")),
    ("user_settings", "telegram_token_enc", ("user_id",)),
    ("user_settings", "webhook_url_enc", ("user_id",)),
    ("user_totp", "secret_enc", ("user_id",)),        # P2-04: секрет второго фактора
)


def _rows(conn, table, column, keys):
    return conn.execute(f"SELECT {', '.join(keys)}, {column} AS blob FROM {table} "
                        f"WHERE {column} IS NOT NULL ORDER BY {', '.join(keys)}").fetchall()


def _scan(conn):
    """(field, key values, blob, state) for every stored value; state is None
    when no current key is configured."""
    ok = SEC.configured()
    for table, column, keys in FIELDS:
        for r in _rows(conn, table, column, keys):
            blob = bytes(r["blob"])
            state = SEC.key_state(blob) if ok else None
            yield f"{table}.{column}", tuple(r[k] for k in keys), blob, state


def health() -> dict:
    """Counts only, for the admin summary. Without a usable current key every
    stored value counts as unreadable -- that is what the users get."""
    out = {"configured": SEC.configured(), "total": 0, SEC.CURRENT: 0, SEC.OLD: 0,
           SEC.UNREADABLE: 0}
    conn = db.get_conn()
    try:
        for _field, _ids, _blob, state in _scan(conn):
            out["total"] += 1
            out[state or SEC.UNREADABLE] += 1
    finally:
        conn.close()
    return out


def rotate(apply: bool = False) -> dict:
    """Re-encrypt with the current key whatever only an old key reads. A dry
    run (the default) changes nothing and reports the same counts. Raises
    SEC.SecretsNotConfigured without a valid current key."""
    SEC._keys()                      # a missing or broken key is an error, not "0 rotated"
    out = {"applied": apply, "total": 0, "rotated": 0, "current": 0, "unreadable": 0,
           "changedMeanwhile": 0, "unreadableFields": []}
    conn = db.get_conn()
    try:
        for field, ids, blob, state in list(_scan(conn)):
            out["total"] += 1
            if state == SEC.CURRENT:
                out["current"] += 1
                continue
            if state == SEC.UNREADABLE:
                out["unreadable"] += 1
                out["unreadableFields"].append({"field": field, "key": list(ids)})
                log.error("stored %s %s cannot be decrypted with any configured key",
                          field, list(ids))
                continue
            if apply:
                table, column = field.split(".")
                keys = next(k for t, c, k in FIELDS if t == table and c == column)
                where = " AND ".join(f"{k} = ?" for k in keys)
                n = conn.execute(f"UPDATE {table} SET {column} = ? WHERE {where} AND {column} = ?",
                                 (SEC.rotate(blob), *ids, blob)).rowcount
                if not n:
                    out["changedMeanwhile"] += 1
                    continue
            out["rotated"] += 1
        if apply:
            conn.commit()
    finally:
        conn.close()
    return out
