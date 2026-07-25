# TwinCursor

A dual-mouse, dual-cursor tool for Windows, designed for ambidextrous users.

TwinCursor lets two physical mice share one PC while each keeps its own cursor position. When you switch to the other mouse, the OS cursor jumps back to where that mouse left off, and a second cursor icon is drawn on a transparent full-screen overlay. A system tray menu provides a per-mouse "Mirror Buttons" toggle that swaps the left and right buttons.

## Requirements

- Windows
- Two physical mice (the app exits if fewer than two are detected)
- [Interception](https://github.com/oblitum/Interception) kernel driver installed
- [uv](https://docs.astral.sh/uv/)

## Deployment

> Note: the deployment process is subject to change and will be reworked.

1. Install the [Interception](https://github.com/oblitum/Interception) driver and reboot.
2. Connect two mice.
3. Run the app:

```
uv run cursor.py
```

On first run, uv automatically creates a virtual environment and installs the locked dependencies before starting the app.

To run with verbose logging:

```
uv run cursor.py --debug
```

## Usage

- The app lives in the system tray. Use **Options → Mouse A / Mouse B → Mirror Buttons** to swap the left/right buttons of a mouse.
- Mirror settings are stored in the registry under `HKCU\SOFTWARE\TwinCursor`.
- Use **Exit** in the tray menu to quit.
