"""Одноразовая Postgres-схема на один процесс тестов -- и уборка за собой.

Импортируется тестовыми модулями ДО первого обращения к базе, только ради
побочного эффекта: выставить NICHE_DB_SCHEMA и зарегистрировать удаление схемы
на выходе из процесса.

Владелец схемы -- процесс, а не модуль: под pytest несколько тестовых файлов
живут в одном процессе, и второй импорт видит NICHE_DB_SCHEMA уже выставленным.
Поэтому решение "наша ли схема" принимается один раз здесь (модуль
импортируется однократно), а DROP делается в atexit -- после того как
отработали все модули, а не в teardown первого из них.

Сами тесты под pytest работают не в схеме процесса, а каждый файл в своей:
tests/conftest.py оборачивает модуль, который импортировал schema_scope, в
module_schema() -- свежая пустая схема на время тестов этого файла, после них
удаляется. Так `pytest tests/` одним процессом видит базу так же, как прогон
по одному файлу на процесс: чужие строки из соседних файлов не мешают.

Две схемы никогда не удаляются:
  * заданная снаружи через NICHE_DB_SCHEMA -- там могут быть реальные данные
    (тогда и module_schema() ничего не подменяет: все файлы работают в ней);
  * своя, если выставлен NICHE_KEEP_TEST_SCHEMA=1 -- чтобы разобрать падение
    (имя схемы печатается при выходе).
"""
import atexit
import contextlib
import itertools
import os
import uuid

OWN_SCHEMA = os.environ.get("NICHE_DB_SCHEMA") is None
if OWN_SCHEMA:
    os.environ["NICHE_DB_SCHEMA"] = f"nichetest_{uuid.uuid4().hex[:8]}"
SCHEMA = os.environ["NICHE_DB_SCHEMA"]

_module_seq = itertools.count(1)

# TestClient шлёт Host: testserver, а HTTP API отвечает только локальным именам
# (interfaces/http/api.py, local_only_guard). Разрешаем его явно через ту же
# настройку, что и у пользователя, -- до импорта api, который читает её один раз.
_extra_hosts = os.environ.get("NF_ALLOWED_HOSTS", "")
if "testserver" not in _extra_hosts.split(","):
    os.environ["NF_ALLOWED_HOSTS"] = ",".join(filter(None, [_extra_hosts, "testserver"]))


def _drop(schema):
    if os.environ.get("NICHE_KEEP_TEST_SCHEMA"):
        print(f"тестовая схема оставлена: NICHE_DB_SCHEMA={schema}")
        return
    import infrastructure.postgres as db
    conn = db.get_conn()
    try:
        conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        conn.commit()
    finally:
        conn.close()


def drop_own_schema():
    """Снести схему, созданную этим процессом. Идемпотентна."""
    if not OWN_SCHEMA:
        return
    _drop(SCHEMA)


def _vector_extension_to_public():
    """Расширение vector -- в public, а не в схеме какого-то файла.

    init_db() создаёт его в текущей схеме: первый init_db случается ещё при
    импорте api.py, в схеме процесса или файла. Оттуда тип vector не виден из
    search_path остальных файлов ("<своя схема>, public"), а DROP SCHEMA ...
    CASCADE удаляет расширение вместе со схемой -- pgvector-тесты тогда молча
    уходят в skip. pgvector перемещаемый, поэтому уже созданное просто
    переносим. Нет расширения в образе -- ничего не делаем, skip честный."""
    import infrastructure.postgres as db
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace "
            "WHERE e.extname = 'vector'").fetchone()
        try:
            if row is None:
                conn.execute("CREATE EXTENSION IF NOT EXISTS vector SCHEMA public")
            elif row[0] != "public":
                conn.execute("ALTER EXTENSION vector SET SCHEMA public")
            conn.commit()
        except Exception:
            conn.rollback()
    finally:
        conn.close()


@contextlib.contextmanager
def module_schema():
    """Своя свежая схема на время одного тестового модуля, потом -- DROP.

    Схема сразу готова (init_db), как раньше в отдельном процессе, где её
    готовил импорт api.py: иначе файл, который сам init_db не зовёт, через
    search_path молча читает public -- локально это реальные данные.

    Со схемой, заданной снаружи, ничего не делает: её не подменяем."""
    if not OWN_SCHEMA:
        yield SCHEMA
        return
    import infrastructure.postgres as db
    _vector_extension_to_public()
    previous = os.environ["NICHE_DB_SCHEMA"]
    schema = f"{SCHEMA}_m{next(_module_seq)}"
    os.environ["NICHE_DB_SCHEMA"] = schema
    try:
        db.init_db()
        yield schema
    finally:
        os.environ["NICHE_DB_SCHEMA"] = previous
        _drop(schema)


atexit.register(drop_own_schema)
