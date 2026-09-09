# TwinCursor

A dual-mouse, dual-cursor tool for Windows, designed for ambidextrous users.

TwinCursor lets two physical mice share one PC while each keeps its own cursor position. When you switch to the other mouse, the OS cursor jumps back to where that mouse left off, and a ghost cursor marks the idle mouse's position. While one mouse is dragging or actively moving, the other mouse moves its own ghost cursor instead of taking over (its clicks are ignored); control switches as soon as the first mouse pauses. A settings window provides a per-mouse "Mirror Buttons" toggle that swaps the left and right buttons, a per-mouse cursor colour, and a global hotkey per mouse to flip the mirror toggle from the keyboard.

TwinCursor also works with a single mouse: set the second device slot to **None** and the app drops into a lightweight single-mouse mode — no input interception on the assigned mouse, no ghost cursor — where the mirror toggle (and its hotkey) simply swaps the buttons using the standard Windows API. Mice that are connected but not assigned to a slot are disabled entirely while the app runs.

Each slot can also be given a colour, which is the quickest way to tell the two mice apart: the mouse in control tints the real Windows cursor, the idle one tints the ghost, and the two colours trade places the moment control switches. Click the small colour chip next to a slot's mirror checkbox to pick from a palette, choose a custom colour, or go back to the untouched system cursor.

The ghost cursor uses your actual system cursor (theme, pointer size and per-monitor DPI scaling are respected), drawn on a tiny per-pixel-alpha layered window.

## Demo

![Two mice, each with its own cursor](demo.gif)

![TwinCursor settings window](screenshot.png)

## Requirements

- Windows 10 or later
- One or two physical mice (the app waits up to 30 seconds for the first one at startup)
- [Interception](https://github.com/oblitum/Interception) kernel driver (TwinCursor offers to install it on first run)
- [uv](https://docs.astral.sh/uv/) (only when running or building from source)

## Installation

1. Download the `TwinCursor-v<version>.exe` asset from the [latest release](https://github.com/weiwei84530/TwinCursor/releases/latest) and run it.
2. If the Interception kernel driver is not installed yet, TwinCursor offers to install the bundled official installer (Windows asks for administrator approval). Restart the computer when prompted, then launch TwinCursor again.
3. The app starts minimized to the system tray.

The driver can also be installed manually: download [Interception](https://github.com/oblitum/Interception/releases/latest), run `install-interception.exe /install` from an administrator command prompt and reboot.

### Running from source

```
uv run -m twincursor
```

On first run, uv automatically creates a virtual environment and installs the locked dependencies before starting the app.

To run with verbose logging:

```
uv run -m twincursor --debug
```

### Building the standalone EXE

```
uv run pyinstaller TwinCursor.spec
```

The single-file executable is written to `dist/`, with the package version in its name — `dist/TwinCursor-v1.1.0.exe` for the current source tree. That version comes from `project.version` in `pyproject.toml`, which is the only place it needs to be bumped.

## Usage

- The app lives in the system tray. Left-click the tray icon (or right-click → **Open Settings**) to open the settings window.
- Each of the two slots (First Mouse / Second Mouse) has a **Device** dropdown listing the connected mice by product name. Pick which physical mouse fills each slot; the second slot can also be set to **None**. With both slots filled the app runs in dual-cursor mode; with one it runs in the simple single-mouse mode. Connected mice that are not assigned to a slot are disabled while the app runs.
- Toggle **Mirror buttons** per slot to swap that mouse's left/right buttons.
- Click the small **colour chip** next to it to colour that mouse's cursor. The popup offers a twelve-colour palette, a **Custom…** picker and **System default** (no tint). The tint keeps the cursor's shape and outline and only replaces its hue. The real cursor is coloured with `SetSystemCursor`, which is a system-wide change: the arrow, hand, resize and "no" cursors are replaced while TwinCursor runs and restored when it exits. The animated busy cursors are left alone, and so are the text I-beam and the crosshair: those invert the pixels behind them instead of painting a colour, which is what keeps them readable on any background, and a coloured copy would lose that. It is rendered at the primary monitor's cursor size, so on a mixed-DPI setup the real cursor keeps that size everywhere. If TwinCursor is killed instead of exiting normally, changing the pointer scheme in Windows' mouse settings restores the cursors.
- Click the **Hotkey** button to record a global keyboard shortcut that flips that slot's mirror toggle; press Esc while recording to disable the hotkey. The first slot defaults to `Ctrl+Alt+M`, the second to none.
- Mirror state, hotkeys and colours belong to the slot (First Mouse / Second Mouse), not the device: swapping or replacing devices leaves each slot's settings in place.
- Each slot remembers the mouse it was given, including slots that were filled automatically, so the two mice do not trade places when the driver enumerates them in a different order after a restart. A mouse plugged in later still fills an empty slot automatically.
- Tick **Start with Windows** to launch TwinCursor automatically when you log on.
- **Restore Defaults** puts everything back to a fresh install: mirroring off, cursor colours cleared, the first slot's hotkey back to `Ctrl+Alt+M` and the second slot's disabled, both slots filled automatically again, and **Start with Windows** turned off.
- **Exit TwinCursor** quits the app, same as the tray menu's **Exit**. Both buttons ask for confirmation first.
- Keyboards that expose an extra mouse-class HID collection (and would otherwise appear in the device dropdowns) are filtered out automatically.
- Settings are stored in the registry under `HKCU\SOFTWARE\TwinCursor`; the logon entry lives under `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` (per user, no administrator rights needed).
- Right-click the tray icon → **Exit** to quit.
- Only one instance can run at a time; a second launch exits immediately.

## Project structure

- `twincursor/__main__.py` — entry point: DPI awareness, single-instance guard, app state, startup, shutdown
- `twincursor/router.py` — Interception event loop, stroke routing and mode switching (the input hot path)
- `twincursor/overlay.py` — ghost-cursor layered window
- `twincursor/cursor_render.py` — cursor bitmap reading, colour tinting and the pristine system-cursor snapshot
- `twincursor/system_cursor.py` — system-wide cursor tint for the mouse in control
- `twincursor/settings_ui.py` — settings window (tkinter)
- `twincursor/tray.py` — system tray icon
- `twincursor/hotkeys.py` — global hotkeys (RegisterHotKey message loop)
- `twincursor/driver_setup.py` — first-run Interception driver installation flow
- `twincursor/autostart.py` — run-at-logon registration
- `twincursor/device_names.py` — human-readable device names via SetupAPI
- `twincursor/settings.py` — registry persistence
- `twincursor/winapi.py` — ctypes Win32 definitions
- `interception_python-1.13.5/` — vendored third-party library (do not modify)
- `interception_installer/` — official Interception driver installer, bundled into the EXE (see its README)
