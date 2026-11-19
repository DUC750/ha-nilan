"""Load the integration's pure modules without Home Assistant.

`register_cache.py`, `device.py`, `device_map.py` and `registers.py` import nothing
from Home Assistant at runtime. Registering the package directory under a stub
package name lets them be imported with their relative imports intact, without
running `custom_components/nilan/__init__.py` (which needs Home Assistant).
"""

from __future__ import annotations

from pathlib import Path
import sys
import types

PACKAGE_DIR = Path(__file__).resolve().parents[2] / "custom_components" / "nilan"

if "nilan" not in sys.modules:
    package = types.ModuleType("nilan")
    package.__path__ = [str(PACKAGE_DIR)]
    sys.modules["nilan"] = package
