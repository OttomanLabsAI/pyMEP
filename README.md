# pyMEP

Automation kit for MEP modelling with [pyRevit](https://github.com/pyrevitlabs/pyRevit).

| path | what it is |
| --- | --- |
| [`pyMEP.extension/`](pyMEP.extension/) | the pyRevit extension - see its [README](pyMEP.extension/README.md) for the full tool guide |

## Install

**Single installer (recommended for colleagues):** download
`pyMEP_Setup_v<version>.exe` from the newest
[GitHub Release](https://github.com/OttomanLabsAI/pyMEP/releases) and run it.
It installs the pyRevit runtime if the machine does not have it, puts pyMEP
in place and attaches it to every installed Revit, per user and without
admin rights. Restart Revit afterwards. See `installer/README.md` for how
it is built and which pyRevit it bundles.


Copy (or clone) `pyMEP.extension/` into your pyRevit extensions directory:

```
%APPDATA%\pyRevit\Extensions\pyMEP.extension\
```

then reload pyRevit.

## Update

Use **pyMEP > Install Update** in the ribbon to download and deploy the
newest tagged version in one go. The deployed version is recorded in
`pyMEP.extension/version.txt` and matches the git tag.

## History

Development started before this repository existed. The 13 surviving
pre-git snapshots were imported as dated commits (branch
`archive/pre-git`, merged into `main`'s ancestry) and tagged
**v0.1.1 - v0.1.13**, so every version of pyMEP - including the
pre-git ones - can be reinstalled from
**Settings > General > Downgrade / reinstall a version (GitHub)**.
Tagged history proper starts at **v0.2.0**.
