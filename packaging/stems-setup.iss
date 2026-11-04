; Stems installer — full portable package (embedded Python + CUDA torch + model + songs)
; Build: ISCC.exe stems-setup.iss  (output in dist\installer\)

#define PackDir "C:\Users\Usuario\Desktop\stems-app-investigacion\dist\stems-pack"

[Setup]
AppId={{7E1A3C52-9B4D-4F6A-8E2B-STEMSAPP0001}
AppName=Stems
AppVersion=1.0
AppPublisher=Stems
DefaultDirName={localappdata}\Stems
DisableProgramGroupPage=yes
; Per-user: no UAC prompt, and the app can write data\ inside its own folder
PrivilegesRequired=lowest
OutputDir=C:\Users\Usuario\Desktop\stems-app-investigacion\dist\installer
OutputBaseFilename=stems-setup
SetupIconFile={#PackDir}\assets\stems.ico
Compression=lzma2
SolidCompression=yes
; ~6.3 GB of content: split into ~2 GB slices (setup.exe + .bin files go together)
DiskSpanning=yes
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
; Excludes: torch's license tree has paths >260 chars that abort the build
; (it adds nothing at runtime). Bytecode caches are excluded too.
Source: "{#PackDir}\*"; DestDir: "{app}"; Excludes: "torch-*.dist-info\licenses\third_party\*,*.pyc,__pycache__\*"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autodesktop}\Stems"; Filename: "{app}\Stems.bat"; WorkingDir: "{app}"; IconFilename: "{app}\assets\stems.ico"
Name: "{autoprograms}\Stems"; Filename: "{app}\Stems.bat"; WorkingDir: "{app}"; IconFilename: "{app}\assets\stems.ico"

[Run]
Filename: "{app}\Stems.bat"; Description: "Launch Stems now"; Flags: postinstall nowait skipifsilent
