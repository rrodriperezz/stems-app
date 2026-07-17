; Instalador de Stems — paquete portable completo (Python embebido + torch CUDA + modelo + canciones)
; Compilar: ISCC.exe stems-setup.iss  (salida en dist\installer\)

#define PackDir "C:\Users\Usuario\Desktop\stems-app-investigacion\dist\stems-pack"

[Setup]
AppId={{7E1A3C52-9B4D-4F6A-8E2B-STEMSAPP0001}
AppName=Stems
AppVersion=1.0
AppPublisher=Stems
DefaultDirName={localappdata}\Stems
DisableProgramGroupPage=yes
; Per-user: sin UAC y la app puede escribir data\ dentro de su carpeta
PrivilegesRequired=lowest
OutputDir=C:\Users\Usuario\Desktop\stems-app-investigacion\dist\installer
OutputBaseFilename=stems-setup
SetupIconFile={#PackDir}\assets\stems.ico
Compression=lzma2
SolidCompression=yes
; Contenido ~6.3 GB: partir en slices de ~2 GB (setup.exe + .bin van juntos)
DiskSpanning=yes
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Files]
; Excludes: el arbol de licencias de torch tiene rutas >260 chars que abortan la compilacion
; (no aporta nada al runtime). Tambien saco caches de bytecode.
Source: "{#PackDir}\*"; DestDir: "{app}"; Excludes: "torch-*.dist-info\licenses\third_party\*,*.pyc,__pycache__\*"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autodesktop}\Stems"; Filename: "{app}\Stems.bat"; WorkingDir: "{app}"; IconFilename: "{app}\assets\stems.ico"
Name: "{autoprograms}\Stems"; Filename: "{app}\Stems.bat"; WorkingDir: "{app}"; IconFilename: "{app}\assets\stems.ico"

[Run]
Filename: "{app}\Stems.bat"; Description: "Abrir Stems ahora"; Flags: postinstall nowait skipifsilent
