# -*- coding: utf-8 -*-
"""GitHub download + deploy engine for the pyMEP self-updater.

Used by pyMEP > Install Update (latest version) and Settings > General >
Downgrade / reinstall a version (any tagged version). Deploys are atomic
and leave NO archive behind:

  1. the live pyMEP.extension folder is renamed to a hidden sibling
     (an atomic same-volume rename - a refused rename means nothing
     changed);
  2. the downloaded zip is extracted into place (single-top-folder and
     loose-file zip layouts both handled) and sanity-checked;
  3. on success the old copy and the downloaded zip are DELETED - no
     more '00 - Superseded' folder; every previous version stays one
     'Downgrade / reinstall' away on GitHub. On ANY failure the old
     copy is renamed straight back.
"""

import datetime
import json
import os
import re
import shutil
import tempfile
import zipfile

import clr
clr.AddReference("System")
from System.Net import ServicePointManager, SecurityProtocolType, WebClient

from pymep_config import (
    EXT_ROOT, get_github_repo, get_github_token, get_downloads_folder,
    get_local_version,
)

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


def _say(log, m):
    if log is not None:
        log(m)


# ---------------------------------------------------------------------------
# GitHub API
# ---------------------------------------------------------------------------
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


def version_key(name):
    """'v1.10.2' -> (1, 10, 2), or None when the tag isn't version-like."""
    m = re.match(r"^[vV]?(\d+(?:\.\d+)*)$", (name or "").strip())
    if not m:
        return None
    return tuple(int(p) for p in m.group(1).split("."))


def zip_url_for(repo, ref):
    """Repo zip download URL for a tag / branch / sha."""
    return "https://api.github.com/repos/{}/zipball/{}".format(repo, ref)


def list_versions(repo=None, token=None):
    """Every version-like tag on the repo, newest first."""
    repo = repo or get_github_repo()
    token = token if token is not None else get_github_token()
    tags = _api_json(repo, "tags?per_page=100", token) or []
    named = [t.get("name") for t in tags if t.get("name")]
    versioned = [n for n in named if version_key(n) is not None]
    versioned.sort(key=version_key, reverse=True)
    return versioned


def resolve_latest(repo=None, token=None):
    """-> (label, zip_url): the latest release, else the newest
    version-like tag, else the default branch."""
    repo = repo or get_github_repo()
    token = token if token is not None else get_github_token()
    try:
        rel = _api_json(repo, "releases/latest", token)
        tag = rel.get("tag_name")
        if tag:
            return tag, zip_url_for(repo, tag)
    except Exception:
        pass
    try:
        versions = list_versions(repo, token)
        if versions:
            return versions[0], zip_url_for(repo, versions[0])
    except Exception:
        pass
    return ("default branch (no releases/tags found)",
            "https://api.github.com/repos/{}/zipball".format(repo))


# ---------------------------------------------------------------------------
# download + deploy
# ---------------------------------------------------------------------------
def download_extension_zip(label, zip_url, repo=None, token=None,
                           downloads=None, log=None):
    """Download the repo zip for ``zip_url`` and repackage JUST the
    pyMEP.extension/ folder into Downloads/pyMEP.extension.zip (staged
    write - a failed download never leaves a truncated zip). Returns the
    zip path, or None when the download failed (reason logged)."""
    repo = repo or get_github_repo()
    token = token if token is not None else get_github_token()
    downloads = downloads or get_downloads_folder()
    dest_zip = os.path.join(downloads, "pyMEP.extension.zip")
    work = tempfile.mkdtemp(prefix="pymep_update_")
    try:
        repo_zip = os.path.join(work, "repo.zip")
        _say(log, "Downloading {} ...".format(zip_url))
        try:
            _client(token).DownloadFile(zip_url, repo_zip)
        except Exception as ex:
            _say(log, "Download failed: {}".format(ex))
            return None

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
            _say(log, "The downloaded zip has no pyMEP.extension folder - "
                      "check the 'github_repo' setting (currently '{}')."
                      .format(repo))
            return None

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
        _say(log, "Downloaded {} - repackaged **{}** files.".format(
            label, count))
        return dest_zip
    finally:
        shutil.rmtree(work, ignore_errors=True)


def deploy_zip(zip_path, log=None):
    """Deploy ``zip_path`` over the live extension. Returns the newly
    installed version string ('' when the zip has no version.txt).
    Raises RuntimeError on any failure - with the previous version
    restored whenever possible, and NOTHING changed when the initial
    rename is refused."""
    ext_dir = EXT_ROOT
    base = os.path.dirname(ext_dir)
    source_name = os.path.basename(ext_dir)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    old_dir = os.path.join(base, ".__old_{}_{}".format(source_name, ts))

    # Atomic same-volume rename: either fully succeeds or does nothing,
    # which is what makes the 'nothing was changed' promise true.
    try:
        os.rename(ext_dir, old_dir)
    except Exception as ex:
        raise RuntimeError(
            "Couldn't move the live extension folder (a file in it is "
            "probably locked by Revit):\n\n{}\n\nNothing was changed. "
            "Close Revit and run supersede_pyExtensions.py instead."
            .format(ex))

    temp_dir = os.path.join(base, ".__extract_" + source_name)
    try:
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
        os.makedirs(temp_dir)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(temp_dir)

        extracted = [os.path.join(temp_dir, n) for n in os.listdir(temp_dir)]
        if len(extracted) == 1 and os.path.isdir(extracted[0]):
            os.rename(extracted[0], ext_dir)
            shutil.rmtree(temp_dir, ignore_errors=True)
        else:
            os.rename(temp_dir, ext_dir)

        if not os.path.isdir(os.path.join(ext_dir, "pyMEP.tab")):
            raise RuntimeError(
                "the extracted zip has no pyMEP.tab folder - not a "
                "pyMEP.extension zip?")
    except Exception as ex:
        shutil.rmtree(temp_dir, ignore_errors=True)
        try:
            if os.path.exists(ext_dir):
                shutil.rmtree(ext_dir)      # discard the bad extract
            os.rename(old_dir, ext_dir)     # put the old version back
            outcome = "The previous version was RESTORED."
        except Exception as ex2:
            outcome = ("Restoring the previous version ALSO failed ({}) - "
                       "recover it manually from:\n{}".format(ex2, old_dir))
        raise RuntimeError("Install failed:\n\n{}\n\n{}".format(ex, outcome))

    _say(log, "Extracted -> {}".format(ext_dir))

    # Success: the old copy and the zip are no longer needed - any
    # version can be reinstalled from GitHub via Downgrade / reinstall.
    try:
        shutil.rmtree(old_dir)
        _say(log, "Removed the previous version's folder.")
    except Exception as ex:
        _say(log, "Couldn't fully remove the old folder (non-fatal, "
                  "delete manually): {} - {}".format(old_dir, ex))
    try:
        os.remove(zip_path)
    except Exception:
        pass

    return get_local_version()
