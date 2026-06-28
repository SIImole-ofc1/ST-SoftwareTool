; ST-Setup.iss â€” Inno Setup 6 installer script for ST-SoftwareTool
;
; Prerequisites:
;   1. Run:  python -m nuitka ... main.py   (see release.ps1)
;      This produces dist_nuitka\main.dist\ that this script packages.
;   2. Download and install Inno Setup 6 from https://jrsoftware.org/isinfo.php
;   3. Run:  .\release.ps1 1.0.9
;      Output: dist_nuitka\installer\ST-SoftwareTool-Setup.exe

#define AppName      "ST-SoftwareTool"
#define AppVersion   "1.0.13"
#define AppPublisher "SIImole"
#define AppURL       "https://st-softwaretool.pages.dev"
#define AppExeName   "ST.exe"

[Setup]
AppId={{F3A72B91-D84C-4E2F-B6C1-9A0E5D3F8B24}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
DefaultDirName={autopf}\{#AppName}
DisableProgramGroupPage=yes
; Close the running app before installing (allows overwrite of locked files)
CloseApplications=yes
RestartApplications=no
; Installer output goes to dist_nuitka\installer\  (created automatically)
OutputDir=dist_nuitka\installer
OutputBaseFilename=ST-SoftwareTool-Setup
SetupIconFile=assets\STsoftwareterminalLOGO.ico
; Logo shown on the left panel (Welcome/Finish pages) and top-right thumbnail
WizardImageFile=assets\STsoftwareterminalLOGO.png.png
WizardSmallImageFile=assets\STsoftwareterminalLOGO.png.png
WizardImageStretch=yes
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; \
    Description: "{cm:CreateDesktopIcon}"; \
    GroupDescription: "{cm:AdditionalIcons}"; \
    Flags: unchecked
Name: "startuprun"; \
    Description: "Launch ST-SoftwareTool automatically with Windows"; \
    GroupDescription: "Startup options:"; \
    Flags: unchecked

[InstallDelete]
; Wipe the previous install before copying new files (ensures clean upgrade)
Type: filesandordirs; Name: "{app}"

[Files]
; Bundle the folder produced by Nuitka
Source: "dist_nuitka\main.dist\*"; \
    DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; \
    Filename: "{app}\{#AppExeName}"; \
    IconFilename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; \
    Filename: "{app}\{#AppExeName}"; \
    IconFilename: "{app}\{#AppExeName}"; \
    Tasks: desktopicon

[Registry]
; Optional: add to Windows startup (only if user ticked the box)
Root: HKCU; \
    Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Run"; \
    ValueType: string; \
    ValueName: "{#AppName}"; \
    ValueData: """{app}\{#AppExeName}"""; \
    Flags: uninsdeletevalue; \
    Tasks: startuprun

[Run]
; shellexec triggers UAC properly for apps with requireAdministrator manifest
Filename: "{app}\{#AppExeName}"; \
    Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; \
    Flags: nowait postinstall skipifsilent shellexec

[UninstallDelete]
; Remove installed app files and user-data folder on uninstall
Type: filesandordirs; Name: "{app}"
Type: filesandordirs; Name: "{userappdata}\{#AppName}"
