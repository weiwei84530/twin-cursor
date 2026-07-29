"""TwinCursor: a dual-mouse, dual-cursor tool for Windows."""

import os
import sys


def resource_path(name: str) -> str:
    """Resolve a bundled asset both in source runs and in a frozen EXE.

    PyInstaller extracts data files to sys._MEIPASS; in source runs the
    assets live in the repository root (the parent of this package).
    """
    base = getattr(sys, "_MEIPASS", None)
    if base is None:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)
