# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build spec for TwinCursor.

Build with: uv run pyinstaller TwinCursor.spec
Produces a single-file windowed executable at dist/TwinCursor-v<version>.exe.
"""

import os
import sys
import tomllib

from PIL import Image

# uv virtual environments do not copy the Tcl runtime, so PyInstaller's
# Tcl/Tk detection fails to initialize Tcl and excludes tkinter from the
# bundle entirely ("tkinter installation is broken"). Point it at the base
# interpreter's Tcl files, same as twincursor.settings_ui does at runtime.
_tcl_root = os.path.join(sys.base_prefix, "tcl")
if os.path.isdir(_tcl_root):
    for _marker, _variable in (("init.tcl", "TCL_LIBRARY"), ("tk.tcl", "TK_LIBRARY")):
        if os.environ.get(_variable):
            continue
        for _candidate in sorted(os.listdir(_tcl_root)):
            _path = os.path.join(_tcl_root, _candidate)
            if os.path.isfile(os.path.join(_path, _marker)):
                os.environ[_variable] = _path
                break

BUILD_DIR = os.path.join(SPECPATH, "build")
os.makedirs(BUILD_DIR, exist_ok=True)

# The EXE name carries the release version. pyproject.toml is its single
# source: uv insists on a static project.version (a virtual project has no
# build backend that could resolve a dynamic one), so a second copy in the
# package would only be something to forget to bump.
with open(os.path.join(SPECPATH, "pyproject.toml"), "rb") as f:
    VERSION = tomllib.load(f)["project"]["version"]

# twincursor/__main__.py cannot be handed to PyInstaller directly: run as a
# top-level script its relative imports lose their package context. Use a
# tiny generated launcher instead.
entry_script = os.path.join(BUILD_DIR, "twincursor_entry.py")
with open(entry_script, "w", encoding="utf-8") as f:
    f.write(
        "import sys\n"
        "from twincursor.__main__ import main\n"
        "sys.exit(main())\n"
    )

# icon.png is 920x1024; pad it to a square first or the .ico comes out
# distorted, then emit all the sizes Explorer and the taskbar ask for.
source = Image.open(os.path.join(SPECPATH, "icon.png")).convert("RGBA")
side = max(source.size)
square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
square.paste(
    source,
    ((side - source.width) // 2, (side - source.height) // 2),
)
icon_path = os.path.join(BUILD_DIR, "icon.ico")
square.save(icon_path, sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])

a = Analysis(
    [entry_script],
    pathex=[SPECPATH],
    binaries=[],
    datas=[
        # resource_path() resolves these at the bundle root.
        (os.path.join(SPECPATH, "cursor.png"), "."),
        (os.path.join(SPECPATH, "icon.png"), "."),
        # Official Interception driver installer, offered on first run
        # when the driver is missing (see twincursor/driver_setup.py).
        (
            os.path.join(
                SPECPATH, "interception_installer", "install-interception.exe"
            ),
            ".",
        ),
    ],
    hiddenimports=[
        # pystray picks its backend at runtime.
        "pystray._win32",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=f"TwinCursor-v{VERSION}",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_path,
)
