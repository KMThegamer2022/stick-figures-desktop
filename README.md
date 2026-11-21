# Stick Figures

Animator-vs-Animation-style stick figures that roam your Windows desktop: they walk, dance,
jump onto windows, climb window sides and screen edges, shove windows around and stomp the
minimise button. Maximised and minimised windows are left alone. The overlay is click-through.

Unofficial fan project, not affiliated with Alan Becker.

## Install
Download `StickFiguresSetup.exe` (or the portable `sticks.exe`) from the latest
[Release](../../releases). Windows SmartScreen may warn because the file is unsigned.

## Run from source
`python sticks.py [count]`. Quit with **Ctrl+Shift+Q**. Set `PRESS_MINIMIZE = False` in
`sticks.py` to stop figures minimising windows.

## Build
Run `build.bat` on Windows (needs Python; Inno Setup 6 for the installer). GitHub Actions does
this on every push; push a tag like `v1.0` to publish a Release.
