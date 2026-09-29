#define MyAppName "DeepSeek 余额监控"
#define MyAppVersion "1.0.0"
#define MyAppExeName "DeepSeekBalance.exe"

[Setup]
AppId={{8F6E5A2C-3D4B-4C6E-9F1A-2B7D8E4C5A6B}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=ds-balance
DefaultDirName={autopf}\DeepSeekBalance
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=installer
OutputBaseFilename=DeepSeekBalance-Setup-1.0.0
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"
Name: "autostart"; Description: "开机自动启动（最小化到托盘）"; GroupDescription: "附加任务:"; Flags: unchecked

[Files]
Source: "dist\DeepSeekBalance\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "DeepSeekBalance"; ValueData: """{app}\{#MyAppExeName}"" --hidden"; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即运行 DeepSeek 余额监控"; Flags: nowait postinstall skipifsilent
