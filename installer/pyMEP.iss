; pyMEP single installer (Inno Setup 6)
;
; One setup for colleagues: carries pyRevit's own installer, runs it silently
; when the pyRevit runtime is not already there, drops pyMEP.extension into
; pyRevit's extensions folder (%APPDATA%\pyRevit\Extensions, the same place
; pyMEP > Install Update uses) and attaches pyRevit to every installed Revit.
; Per-user, no admin rights. Uninstall removes pyMEP only; pyRevit stays.
;
; Built by installer\build.ps1, which stages the extension and the pyRevit
; installer under installer\stage\ and passes the version in:
;   ISCC.exe /DAppVersion=1.249.0 /DPyRevitSetup=pyRevit_6.5.3_signed.exe pyMEP.iss

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef PyRevitSetup
  #define PyRevitSetup "pyRevit_setup.exe"
#endif

[Setup]
AppId={{7D3F0C6A-3B4E-4C0B-9C1E-5A2B7E2F1A10}
AppName=pyMEP
AppVersion={#AppVersion}
AppVerName=pyMEP v{#AppVersion}
AppPublisher=Glent Group
DefaultDirName={userappdata}\pyRevit\Extensions\pyMEP.extension
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
OutputBaseFilename=pyMEP_Setup_v{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=pyMEP v{#AppVersion} (Revit extension)
SetupLogging=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "reinstallpyrevit"; Description: "Reinstall the pyRevit runtime even if it is already present"; Flags: unchecked

[Files]
; The extension itself (staged from the repo by build.ps1).
Source: "stage\pyMEP.extension\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
; pyRevit's installer, unpacked to the temp folder and run below.
Source: "stage\{#PyRevitSetup}"; DestDir: "{tmp}"; Flags: deleteafterinstall

[Run]
; 1) pyRevit runtime, silently, when missing (or when asked to reinstall).
Filename: "{tmp}\{#PyRevitSetup}"; Parameters: "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART"; \
  StatusMsg: "Installing the pyRevit runtime (a minute or two)..."; Flags: waituntilterminated; \
  Check: NeedsPyRevit
; 2) Attach pyRevit to every installed Revit for this user (idempotent).
Filename: "{code:PyRevitCli}"; Parameters: "attach master default --installed"; \
  StatusMsg: "Attaching pyRevit to the installed Revit versions..."; \
  Flags: runhidden waituntilterminated skipifdoesntexist; Check: HasPyRevitCli

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Messages]
FinishedLabelNoIcons=pyMEP v{#AppVersion} is installed.%n%nStart (or restart) Revit: pyMEP appears as its own ribbon tab. Later updates can come from pyMEP > Install Update inside Revit, or from a newer pyMEP setup.

[Code]
function PyRevitHome(): String;
begin
  Result := ExpandConstant('{userappdata}\pyRevit-Master');
end;

function PyRevitCli(Param: String): String;
begin
  Result := PyRevitHome() + '\bin\pyrevit.exe';
end;

function HasPyRevitCli(): Boolean;
begin
  Result := FileExists(PyRevitCli(''));
end;

function NeedsPyRevit(): Boolean;
begin
  // The pyRevit installer clones the runtime to %APPDATA%\pyRevit-Master.
  Result := WizardIsTaskSelected('reinstallpyrevit') or (not DirExists(PyRevitHome()));
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
  if CheckForMutexes('Revit') then
    MsgBox('Revit seems to be running. The install will still work, but pyMEP only shows up after Revit is restarted.', mbInformation, MB_OK);
end;
