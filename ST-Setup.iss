; ST-Setup.iss — Inno Setup 6 installer script for ST-SoftwareTool
;
; Prerequisites:
;   1. Run:  pyinstaller ST.spec
;      This produces the dist\ST\ folder that this script packages.
;   2. Download and install Inno Setup 6 from https://jrsoftware.org/isinfo.php
;   3. Open this file in Inno Setup Compiler and click Build > Compile
;      Output: dist\installer\ST-Setup-1.0.0.exe

#define AppName      "ST-SoftwareTool"
#define AppVersion   "1.0.1"
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
; Installer output goes to dist\installer\  (created automatically)
OutputDir=dist\installer
OutputBaseFilename=ST-Setup-{#AppVersion}
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
; Show a "Launch ST-SoftwareTool" checkbox on the final page
; (handled in [Run] section below)

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

[Files]
; Bundle the entire dist\ST\ folder produced by PyInstaller
Source: "dist\ST\*"; \
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
; Remove the user-data folder created in %APPDATA% on uninstall
Type: filesandordirs; \
    Name: "{userappdata}\{#AppName}"
