"""Shared pytest wiring: the whole suite runs as ONE pytest process, yet every
test file sees the world it would see running alone.

Two kinds of per-file isolation, both switched on for the duration of one
test module and switched off before the next:

* Postgres-backed files (they `import schema_scope`) get a fresh, empty schema
  of their own (schema_scope.module_schema) -- rows left behind by one file
  never show up in another file's queries.
* Files that run application code against in-process doubles expose a
  module-level `DOUBLES` (tests/module_doubles.py); their private module world
  is what sys.modules shows while their tests run, so lazy imports inside the
  application code resolve to the doubles too -- and nothing leaks out.
"""
import contextlib

import pytest


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
        yield
