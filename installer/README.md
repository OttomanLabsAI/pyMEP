# pyMEP single installer

`pyMEP_Setup_v<version>.exe` gives colleagues one install: it carries the
pyRevit installer, runs it silently when the pyRevit runtime is missing,
puts `pyMEP.extension` into `%APPDATA%\pyRevit\Extensions` (the folder
pyRevit scans, and the one **pyMEP > Install Update** already uses), and
attaches pyRevit to every installed Revit for that user. Per user, no admin
rights, no pyRevit knowledge needed. Uninstall removes pyMEP only.

## Getting one

Every push to `main` is tagged, and the `tag-release` workflow then builds
the installer on a Windows runner and publishes it on the GitHub Release for
that tag (Releases page > the version > Assets). Send colleagues that file.

## Building locally

Needs Windows, PowerShell and [Inno Setup 6](https://jrsoftware.org/isinfo.php).

```powershell
powershell -ExecutionPolicy Bypass -File installer\build.ps1
```

The script stages the extension, downloads the pinned pyRevit installer
from the pyrevitlabs/pyRevit GitHub release and compiles `pyMEP.iss` into
`dist\pyMEP_Setup_v<version>.exe`. Offline, hand it a downloaded pyRevit
installer with `-PyRevitSetupPath`.

## Which pyRevit

`installer\pyrevit-version.txt` pins the pyRevit release that gets bundled.
pyRevit's release tags carry the full build string (`v6.5.3.26176+2017`),
so the pin is matched as a prefix and the newest build of that version is
taken; put a full tag in the file to pin one exact build. Change it when
you move the office to a newer pyRevit. The build fails loudly, listing
the newest tags, if nothing matches or the release has no
`pyRevit_<build>_signed.exe` asset.

## What the installer does, step by step

1. Copies the extension to `%APPDATA%\pyRevit\Extensions\pyMEP.extension`
   (overwriting an older pyMEP there; settings live in
   `%APPDATA%\pyRevit\pyMEP_settings.json` and are untouched).
2. If `%APPDATA%\pyRevit-Master` is missing, or the "reinstall pyRevit" box
   was ticked, runs the bundled pyRevit installer with
   `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART`.
3. Runs `pyrevit attach master default --installed` so every Revit version
   on the machine loads pyRevit for this user.
4. Asks for a Revit restart. pyMEP then appears as its own tab.

If a future pyRevit release changes its installer technology, the silent
switches in `pyMEP.iss` are the one place to update.
