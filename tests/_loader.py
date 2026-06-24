"""Import the Digi ``api`` module (and its const/models) without triggering the
package ``__init__``, which imports Home Assistant.

This lets the pure HTML-parsing tests run anywhere — including dev machines that
cannot install Home Assistant — while remaining valid in CI.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PKG_DIR = os.path.join(_ROOT, "custom_components", "digi")


def load_api():
    """Load and return the ``custom_components.digi.api`` module standalone."""
    pkg_name = "digi_api_under_test"
    if pkg_name in sys.modules:
        return sys.modules[f"{pkg_name}.api"]

    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [_PKG_DIR]
    sys.modules[pkg_name] = pkg

    def _load(mod_name: str) -> types.ModuleType:
        spec = importlib.util.spec_from_file_location(
            f"{pkg_name}.{mod_name}", os.path.join(_PKG_DIR, f"{mod_name}.py")
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"{pkg_name}.{mod_name}"] = module
        spec.loader.exec_module(module)
        return module

    _load("const")
    _load("models")
    return _load("api")
