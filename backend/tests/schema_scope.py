"""Одноразовая Postgres-схема на один процесс тестов -- и уборка за собой.

Импортируется тестовыми модулями ДО первого обращения к базе, только ради
побочного эффекта: выставить NICHE_DB_SCHEMA и зарегистрировать удаление схемы
на выходе из процесса.

Владелец схемы -- процесс, а не модуль: под pytest несколько тестовых файлов
живут в одном процессе, и второй импорт видит NICHE_DB_SCHEMA уже выставленным.
Поэтому решение "наша ли схема" принимается один раз здесь (модуль
импортируется однократно), а DROP делается в atexit -- после того как
отработали все модули, а не в teardown первого из них.

Две схемы никогда не удаляются:
  * заданная снаружи через NICHE_DB_SCHEMA -- там могут быть реальные данные;
  * своя, если выставлен NICHE_KEEP_TEST_SCHEMA=1 -- чтобы разобрать падение
    (имя схемы печатается при выходе).
"""
import atexit
import os
import uuid

OWN_SCHEMA = os.environ.get("NICHE_DB_SCHEMA") is None
if OWN_SCHEMA:
    os.environ["NICHE_DB_SCHEMA"] = f"nichetest_{uuid.uuid4().hex[:8]}"
SCHEMA = os.environ["NICHE_DB_SCHEMA"]

# TestClient шлёт Host: testserver, а HTTP API отвечает только локальным именам
# (interfaces/http/api.py, local_only_guard). Разрешаем его явно через ту же
# настройку, что и у пользователя, -- до импорта api, который читает её один раз.
_extra_hosts = os.environ.get("NF_ALLOWED_HOSTS", "")
if "testserver" not in _extra_hosts.split(","):
    os.environ["NF_ALLOWED_HOSTS"] = ",".join(filter(None, [_extra_hosts, "testserver"]))


def drop_own_schema():
    """Снести схему, созданную этим процессом. Идемпотентна."""
    if not OWN_SCHEMA:
        return
    if os.environ.get("NICHE_KEEP_TEST_SCHEMA"):
        print(f"тестовая схема оставлена: NICHE_DB_SCHEMA={SCHEMA}")
        return
    import infrastructure.postgres as db
    conn = db.get_conn()
    try:
        conn.execute(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE')
        conn.commit()
    finally:
        conn.close()


atexit.register(drop_own_schema)
