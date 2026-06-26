; Aura-Translat installer script for Inno Setup

#define MyAppName "Aura-Translat"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Aura Neo"
#define MyAppURL "https://auraneo.fr"
#define MyAppExeName "Aura-Translat.exe"

[Setup]
AppId={{B85A0B7D-46B4-4A75-A4A9-CB57E897A720}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={code:GetDefaultInstallDir}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
LicenseFile=
InfoBeforeFile=
InfoAfterFile=
OutputDir=..\dist-installer
OutputBaseFilename=Aura-Translat-installer-{#MyAppVersion}
SetupIconFile=..\icon\Aura-Translat.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DisableProgramGroupPage=no
UsePreviousAppDir=no
UsePreviousGroup=no
DisableDirPage=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "Creer un raccourci sur le Bureau"; GroupDescription: "Raccourcis:"

[Files]
Source: "..\dist\Aura-Translat.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\icon\Aura-Translat.ico"; DestDir: "{app}\icon"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\icon\Aura-Translat.ico"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon; IconFilename: "{app}\icon\Aura-Translat.ico"
Name: "{autoprograms}\{#MyAppName}\Desinstaller {#MyAppName}"; Filename: "{uninstallexe}"; IconFilename: "{app}\icon\Aura-Translat.ico"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Lancer {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
function GetDefaultInstallDir(Value: string): string;
var
  SourceDrive: string;
begin
  SourceDrive := ExtractFileDrive(ExpandConstant('{srcexe}'));
  if SourceDrive = '' then
    SourceDrive := ExpandConstant('{userdocs}');

  if Length(SourceDrive) = 2 then
    Result := SourceDrive + '\' + '{#MyAppName}'
  else
    Result := SourceDrive + '\{#MyAppName}';
end;
