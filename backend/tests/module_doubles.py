"""Test doubles for infrastructure modules, without leaking them into sys.modules.

Some test files run the real application code against a sqlite double of
infrastructure.postgres (and stubs for YouTube, embeddings, ...). Writing the
double straight into sys.modules at import time leaks it into every file that
pytest collects afterwards in the same process. Instead:

    DOUBLES = ModuleDoubles({"infrastructure.postgres": fake_db})
    AL, = DOUBLES.load("application.alerts")

`load` imports the application modules into a *private* module world --
every application.* / infrastructure.* entry is dropped from sys.modules, the
doubles are put in, the modules are imported, the private world is recorded,
and sys.modules is put back exactly as it was. The returned modules keep
pointing at the doubles; nobody else ever sees them.

Application code that imports lazily (inside a function) resolves through
sys.modules at call time, so the private world is switched back on while that
file's tests run: tests/conftest.py does it for any test module with a
module-level `DOUBLES`, and a file run as a plain script wraps its own runner
in `with DOUBLES.active():`.

Only application.* and infrastructure.* are swapped. domain.* is pure (it
imports neither), so it is shared between worlds.
"""
import contextlib
import importlib
import sys

OWNED = ("application", "infrastructure")


def _is_owned(name):
    return any(name == p or name.startswith(p + ".") for p in OWNED)


def _take_owned():
    """Remove every owned entry from sys.modules and return them."""
    taken = {k: v for k, v in sys.modules.items() if _is_owned(k)}
    for k in taken:
        del sys.modules[k]
    return taken


def _put_back(saved):
    _take_owned()
    sys.modules.update(saved)


class ModuleDoubles:
    def __init__(self, doubles):
        stray = [k for k in doubles if not _is_owned(k)]
        if stray:
            raise ValueError(f"only {OWNED} modules can be doubled, got {stray}")
        self.doubles = dict(doubles)
        self.world = None

    def load(self, *names):
        """Import `names` against the doubles; return the modules in order."""
        saved = _take_owned()
        try:
            sys.modules.update(self.doubles)
            # `from pkg import sub` looks for an attribute on the package
            # first -- make the doubles reachable that way too.
            for name, mod in self.doubles.items():
                parent, _, leaf = name.rpartition(".")
                if parent:
                    setattr(importlib.import_module(parent), leaf, mod)
            mods = [importlib.import_module(n) for n in names]
            self.world = {k: v for k, v in sys.modules.items() if _is_owned(k)}
        finally:
            _put_back(saved)
        return mods

    @contextlib.contextmanager
    def active(self):
        """Make the private world the one sys.modules shows, then undo it."""
        if self.world is None:
            raise RuntimeError("ModuleDoubles.active() before load()")
        saved = _take_owned()
        try:
            sys.modules.update(self.world)
            yield
        finally:
            _put_back(saved)
