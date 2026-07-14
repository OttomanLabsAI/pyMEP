# pyMEP - working agreements

pyRevit extension for MEP modelling. Everything under
`pyMEP.extension/pyMEP.tab/` and `pyMEP.extension/lib/` runs under
IronPython 2.7 inside Revit: no f-strings, .NET interop via `clr`,
`.format()` strings, `# -*- coding: utf-8 -*-` headers.
`pyMEP.extension/conduit_analysis/` is CPython-only (external Python).

## Release flow (standing instructions from the repo owner)

- When a piece of work is DONE, push it to `main` (fast-forward from the
  working branch) without waiting to be asked - unless told otherwise.
- Every push to `main` is a release: bump `pyMEP.extension/version.txt`
  to the next `v0.<x>` first (increment x by one, e.g. v0.3.0 -> v0.4.0;
  never reuse a version) unless told to use a different version.
- Tags: the `tag-release` GitHub Action tags every main push with
  whatever `version.txt` says at that commit - bumping version.txt IS
  the tagging step. Tag pushes from the dev environment are blocked
  (git proxy returns 403 on refs/tags); never try to push tags
  directly. For a retroactive tag, dispatch the `tag-release` workflow
  with its `tag` + `sha` inputs.
- `Setup > Download Latest` in the ribbon downloads the newest
  release/tag (falling back to the default branch), so version.txt and
  the tags are what make self-update work.
