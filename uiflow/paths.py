"""Where uiflow stores its data: workflows/, orchestrator.db, screenshots.

Override with the UIFLOW_HOME env var - e.g. when running the PyInstaller-
bundled exe, whose __file__ points into a throwaway temp extraction dir
instead of the real install location, so it can't be used to find existing
data the way the from-source case always could.

Without UIFLOW_HOME: a normal from-source run keeps resolving against the
repo checkout (unchanged behavior); a frozen exe falls back to the current
working directory, so `cd /path/to/data && ./uiflow-studio studio` also
works without setting anything.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _default_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path.cwd()
    return Path(__file__).resolve().parent.parent


_override = os.environ.get("UIFLOW_HOME")
DATA_ROOT = Path(_override).resolve() if _override else _default_root()
