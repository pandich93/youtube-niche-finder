"""Shared pytest wiring: the whole suite runs as ONE pytest process, yet every
test file sees the world it would see running alone.

Two kinds of per-file isolation, both switched on for the duration of one
test module and switched off before the next:

* Postgres-backed files (they `import schema_scope`) get a fresh, empty schema
  of their own (schema_scope.module_schema) -- rows left behind by one file
  never show up in another file's queries. The file is recognised by its
  module attribute `schema_scope`, so it has to be exactly a top-level
  `import schema_scope` -- not `from schema_scope import ...`, not an alias,
  not via a helper module (tests/test_schema_scope.py guards this).
* Files that run application code against in-process doubles expose a
  module-level `DOUBLES` (tests/module_doubles.py); their private module world
  is what sys.modules shows while their tests run, so lazy imports inside the
  application code resolve to the doubles too -- and nothing leaks out.

After each file, process-wide caches that separate processes used to reset
for free are reset too (reset_process_caches): the maturity curve that
application.maturity_curve read from that file's schema, and the one it made
active in domain.metrics, stay in force for up to CACHE_TTL_SEC otherwise.
Not reset, as they do not depend on a file's data: the Postgres connection
pool, infrastructure.postgres.schema._pgvector_available (re-set by every
init_db; the extension is database-wide), the lazily imported fastembed module.
"""
import contextlib
import sys

import pytest


def reset_process_caches():
    """Forget state a test file left in process-wide caches. Only touches
    modules already imported -- never imports application code itself."""
    mc = sys.modules.get("application.maturity_curve")
    if mc is not None:
        mc.reset_cache()
    metrics = sys.modules.get("domain.metrics")
    if metrics is not None:
        metrics.set_maturity_curve(None)


@pytest.fixture(autouse=True, scope="module")
def _per_file_isolation(request):
    module = request.module
    with contextlib.ExitStack() as stack:
        scope = getattr(module, "schema_scope", None)
        if scope is not None:
            stack.enter_context(scope.module_schema())
        doubles = getattr(module, "DOUBLES", None)
        if doubles is not None:
            stack.enter_context(doubles.active())
        stack.callback(reset_process_caches)  # runs first on exit, inside the file's world
        yield
    reset_process_caches()  # and again in the shared world the next file sees
