[Setup]
AppName=Stick Figures
AppVersion=1.0
DefaultDirName={autopf}\StickFigures
DefaultGroupName=Stick Figures
OutputDir=dist
OutputBaseFilename=StickFiguresSetup
PrivilegesRequired=lowest

[Tasks]
Name: autostart; Description: "Start with Windows"; Flags: unchecked

[Files]
Source: "dist\sticks.exe"; DestDir: "{app}"

[Icons]
Name: "{group}\Stick Figures"; Filename: "{app}\sticks.exe"
Name: "{userstartup}\Stick Figures"; Filename: "{app}\sticks.exe"; Tasks: autostart

[Run]
Filename: "{app}\sticks.exe"; Description: "Launch now"; Flags: nowait postinstall skipifsilent
