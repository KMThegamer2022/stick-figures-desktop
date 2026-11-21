@echo off
rem Builds dist\sticks.exe, then dist\StickFiguresSetup.exe if Inno Setup 6 is installed.
cd /d "%~dp0"
py -m pip install --quiet pyinstaller pystray pillow || goto :err
py -m PyInstaller --onefile --noconsole --name sticks sticks.py || goto :err
set ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe
if not exist "%ISCC%" goto :noinno
"%ISCC%" installer.iss || goto :err
echo Done: dist\StickFiguresSetup.exe
goto :eof
:noinno
echo dist\sticks.exe is ready. Install Inno Setup 6 (jrsoftware.org) and rerun for a real installer.
goto :eof
:err
echo Build failed.
exit /b 1
