"""Guard: the sqlite-double test files must not leak their doubles.

Several test files swap infrastructure.postgres (and friends) for in-process
doubles. The whole suite runs as ONE pytest process, so importing such a file
must leave sys.modules exactly as it found it for every application.* /
infrastructure.* entry -- otherwise the Postgres-backed files collected after
it would silently talk to sqlite (or crash on a missing attribute).

Run: pytest tests/test_module_doubles.py
"""
import importlib.util
import os
import re
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

DOUBLE_FILES = [
    "test_alerts.py",
    "test_inspection.py",
    "test_library.py",
    "test_metadata_review.py",
    "test_top_tags.py",
]

OWNED = ("application", "infrastructure")


def _owned():
    return {k: v for k, v in sys.modules.items()
            if any(k == p or k.startswith(p + ".") for p in OWNED)}


@pytest.mark.parametrize("fname", DOUBLE_FILES)
def test_importing_a_double_file_leaves_sys_modules_untouched(fname):
    before = _owned()
    name = "_doubles_probe_" + fname[:-3]
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, fname))
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
        after = _owned()
        added = sorted(after.keys() - before.keys())
        removed = sorted(before.keys() - after.keys())
        swapped = sorted(k for k in before.keys() & after.keys() if before[k] is not after[k])
        assert not (added or removed or swapped), (
            f"{fname} leaked into sys.modules: added={added} removed={removed} swapped={swapped}")
    finally:
        # Put things back even on failure, so one leaky file does not break
        # every test that runs after this one.
        for k in list(_owned()):
            if k not in before:
                del sys.modules[k]
        sys.modules.update(before)
        sys.modules.pop(name, None)


# Direct writes are how doubles leaked before: `sys.modules[...] = fake` at
# import time, never undone. Doubles go through tests/module_doubles.py.
_DIRECT_WRITE = re.compile(r"sys\.modules\s*(\[[^\]]+\]\s*=|\.(setdefault|update|pop)\()")


def test_no_test_file_writes_sys_modules_directly():
    offenders = []
    for fname in sorted(os.listdir(HERE)):
        if not (fname.startswith("test_") and fname.endswith(".py")):
            continue
        if fname == os.path.basename(__file__):
            continue
        with open(os.path.join(HERE, fname), encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                if _DIRECT_WRITE.search(line):
                    offenders.append(f"{fname}:{lineno}: {line.strip()}")
    assert not offenders, "use tests/module_doubles.py instead:\n" + "\n".join(offenders)
