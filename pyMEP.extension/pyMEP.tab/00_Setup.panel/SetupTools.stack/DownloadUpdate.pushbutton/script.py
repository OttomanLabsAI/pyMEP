# -*- coding: utf-8 -*-
"""Download Latest - fetch the newest published version of pyMEP.extension
from GitHub into the Downloads folder.

Flow:
  1. Ask the GitHub API for the latest release; fall back to the newest
     tag, then to the default branch.
  2. Download the repository zip for that ref.
  3. Repackage JUST the pyMEP.extension/ folder into
     Downloads/pyMEP.extension.zip - the exact single-top-level-folder
     layout that Install Update and supersede_pyExtensions.py expect.

Settings keys (pyMEP_settings.json): 'github_repo' ("owner/repo"),
'github_token' (optional - private repos / API rate limits),
'update_downloads_folder' (optional override of the Downloads folder).
"""

__title__  = "Download Latest"
__author__ = "Glent Group"

import json
import os
import re
import shutil
import sys
import tempfile
import zipfile

# Force-reload pymep_* libs so edits on disk always take effect.
for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

from pyrevit import forms, script

from pymep_config import (
    get_github_repo, get_github_token, get_downloads_folder,
    get_local_version,
)
from pymep_log import Logger

import clr
clr.AddReference("System")
from System.Net import ServicePointManager, SecurityProtocolType, WebClient

output = script.get_output()
log = Logger(output, "DownloadLatest")

# GitHub requires TLS 1.2+; older .NET runtimes don't enable it by default.
try:
    ServicePointManager.SecurityProtocol = (
        ServicePointManager.SecurityProtocol | SecurityProtocolType.Tls12)
except Exception:
    pass

# Store, don't compress, if this engine has no zlib (rare).
try:
    import zlib  # noqa: F401
    _COMPRESSION = zipfile.ZIP_DEFLATED
except ImportError:
    _COMPRESSION = zipfile.ZIP_STORED


def _client(token, accept=None):
    """A WebClient with the headers GitHub wants (fresh per request -
    WebClient clears headers after each call)."""
    wc = WebClient()
    wc.Headers.Add("User-Agent", "pyMEP-updater")
    if accept:
        wc.Headers.Add("Accept", accept)
    if token:
        wc.Headers.Add("Authorization", "token " + token)
    return wc


def _api_json(repo, path, token):
    url = "https://api.github.com/repos/{}/{}".format(repo, path)
    wc = _client(token, accept="application/vnd.github+json")
    return json.loads(wc.DownloadString(url))


def _version_key(name):
    """'v0.10.2' -> (0, 10, 2), or None when the tag isn't version-like."""
    m = re.match(r"^[vV]?(\d+(?:\.\d+)*)$", (name or "").strip())
    if not m:
        return None
    return tuple(int(p) for p in m.group(1).split("."))


def _newest_tag(tags):
    """Pick the highest version-like tag name; the GitHub /tags endpoint
    has NO ordering guarantee (and string-sorts v0.9 above v0.10), so
    never trust tags[0]. Falls back to the first entry only when no tag
    parses as a version."""
    best_name = None
    best_key = None
    for t in tags:
        name = t.get("name")
        key = _version_key(name)
        if key is not None and (best_key is None or key > best_key):
            best_key = key
            best_name = name
    if best_name is None and tags:
        best_name = tags[0].get("name")
    return best_name


def _resolve_latest(repo, token):
    """-> (label, zip_url): the latest release, else the newest
    version-like tag, else the default branch."""
    try:
        rel = _api_json(repo, "releases/latest", token)
        tag = rel.get("tag_name")
        if tag:
            return tag, "https://api.github.com/repos/{}/zipball/{}".format(
                repo, tag)
    except Exception:
        pass
    try:
        tags = _api_json(repo, "tags?per_page=100", token)
        tag = _newest_tag(tags or [])
        if tag:
            return tag, ("https://api.github.com/repos/{}/zipball/{}"
                         .format(repo, tag))
    except Exception:
        pass
    return ("default branch (no releases/tags found)",
            "https://api.github.com/repos/{}/zipball".format(repo))


repo = get_github_repo()
token = get_github_token()
downloads = get_downloads_folder()
local_ver = get_local_version()

log("### Download Latest")
log("Repo: **{}**".format(repo))

if not os.path.isdir(downloads):
    forms.alert(
        "Downloads folder not found:\n{}\n\nSet the "
        "'update_downloads_folder' key in pyMEP_settings.json to the "
        "folder you want updates saved to.".format(downloads),
        exitscript=True)
log("Installed version: **{}**".format(local_ver or "(no version.txt)"))

label, zip_url = _resolve_latest(repo, token)
log("Latest on GitHub: **{}**".format(label))

if local_ver and local_ver == label:
    if forms.alert(
            "You already have the latest version ({}).\n\n"
            "Download it again anyway?".format(label),
            title="Up to date",
            options=["Download anyway", "Cancel"]) != "Download anyway":
        log("Already up to date - cancelled.")
        log.close()
        script.exit()

dest_zip = os.path.join(downloads, "pyMEP.extension.zip")
if os.path.exists(dest_zip):
    if forms.alert(
            "Downloads already contains pyMEP.extension.zip:\n\n{}\n\n"
            "Overwrite it with version {}?".format(dest_zip, label),
            title="Overwrite?",
            options=["Overwrite", "Cancel"]) != "Overwrite":
        log("Not overwriting the existing zip - cancelled.")
        log.close()
        script.exit()

work = tempfile.mkdtemp(prefix="pymep_update_")
try:
    repo_zip = os.path.join(work, "repo.zip")
    log("Downloading {} ...".format(zip_url))
    try:
        _client(token).DownloadFile(zip_url, repo_zip)
    except Exception as ex:
        forms.alert(
            "Download failed:\n\n{}\n\nIf the repository is private, set "
            "the 'github_token' key in pyMEP_settings.json to a GitHub "
            "personal-access token with repo read access.".format(ex),
            exitscript=True)

    # GitHub repo zips wrap everything in '<owner>-<repo>-<sha>/'; find the
    # pyMEP.extension folder inside (or a root that IS the extension).
    unpack = os.path.join(work, "unpacked")
    with zipfile.ZipFile(repo_zip) as zf:
        zf.extractall(unpack)

    ext_src = None
    for entry in sorted(os.listdir(unpack)):
        p = os.path.join(unpack, entry)
        if not os.path.isdir(p):
            continue
        inner = os.path.join(p, "pyMEP.extension")
        if os.path.isdir(inner):
            ext_src = inner
            break
        if os.path.isdir(os.path.join(p, "pyMEP.tab")):
            ext_src = p
            break
    if ext_src is None:
        forms.alert(
            "The downloaded zip doesn't contain a pyMEP.extension folder - "
            "check the 'github_repo' setting (currently '{}').".format(repo),
            exitscript=True)

    # Repackage with a single top-level 'pyMEP.extension/' folder - what
    # the installers expect. Built in the temp dir first and only moved
    # onto Downloads/pyMEP.extension.zip when complete, so a failure can
    # never leave a truncated zip where the installers would find it.
    count = 0
    staged_zip = os.path.join(work, "pyMEP.extension.zip")
    zf_out = zipfile.ZipFile(staged_zip, "w", _COMPRESSION)
    try:
        for root, dirs, files in os.walk(ext_src):
            for fn in files:
                full = os.path.join(root, fn)
                rel = os.path.relpath(full, ext_src).replace(os.sep, "/")
                zf_out.write(full, "pyMEP.extension/" + rel)
                count += 1
    finally:
        zf_out.close()
    if os.path.exists(dest_zip):
        os.remove(dest_zip)
    shutil.move(staged_zip, dest_zip)

    log("Repackaged **{}** files into **{}**.".format(count, dest_zip))
    forms.alert(
        "Downloaded pyMEP.extension {}.\n\nSaved to:\n{}\n\nNow run "
        "Install Update (next to this button) to deploy it - or "
        "supersede_pyExtensions.py outside Revit.".format(label, dest_zip),
        title="Download complete")
finally:
    shutil.rmtree(work, ignore_errors=True)
    log.close()
