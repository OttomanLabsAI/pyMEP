# pyMEP

Automation kit for MEP modelling with [pyRevit](https://github.com/pyrevitlabs/pyRevit).

| path | what it is |
| --- | --- |
| [`pyMEP.extension/`](pyMEP.extension/) | the pyRevit extension - see its [README](pyMEP.extension/README.md) for the full tool guide |
| [`supersede_pyExtensions.py`](supersede_pyExtensions.py) | deploy script: supersedes the live extension folder(s) with fresh `*.extension.zip` downloads (run outside Revit; the in-Revit equivalent is the Setup panel's **Install Update** button) |

## Install

Copy (or clone) `pyMEP.extension/` into your pyRevit extensions directory:

```
%APPDATA%\pyRevit\Extensions\pyMEP.extension\
```

then reload pyRevit.

## Update

Use **pyMEP > Install Update** in the ribbon to download and deploy the
newest tagged version in one go (or `supersede_pyExtensions.py` outside
Revit). The deployed version is recorded in
`pyMEP.extension/version.txt` and matches the git tag.

## History

Development started before this repository existed. The 13 surviving
pre-git snapshots were imported as dated commits (branch
`archive/pre-git`, merged into `main`'s ancestry) and tagged
**v0.1.1 - v0.1.13**, so every version of pyMEP - including the
pre-git ones - can be reinstalled from
**Settings > General > Downgrade / reinstall a version (GitHub)**.
Tagged history proper starts at **v0.2.0**.
