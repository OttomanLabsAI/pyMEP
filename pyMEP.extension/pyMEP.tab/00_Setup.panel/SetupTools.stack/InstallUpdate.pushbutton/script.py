# -*- coding: utf-8 -*-
"""Install Update - fetch the newest published pyMEP.extension from
GitHub and deploy it over the live extension, in one go.

Flow (engine in lib/pymep_update.py):
  1. Resolve the latest release (else newest tag, else default branch)
     and compare against the installed version.txt.
  2. Download + repackage the pyMEP.extension folder into
     Downloads/pyMEP.extension.zip (staged write). If the download
     fails but Downloads already holds a pyMEP.extension.zip, offers
     to install that instead.
  3. Deploy atomically: the live folder is set aside with a rename,
     the new version extracted into place, and on success the old
     folder and the zip are deleted - no superseded archive; older
     versions stay reinstallable via Settings > General >
     Downgrade / reinstall. Any failure restores the old version.
  4. Offers to reload pyRevit so the new version is live.

Settings keys (pyMEP_settings.json): 'github_repo' ("owner/repo"),
'github_token' (optional - private repos / API rate limits),
'update_downloads_folder' (optional override of the Downloads folder).
"""

__title__  = "Install Update"
__author__ = "Glent Group"

import datetime
import os
import sys

# Force-reload pymep_* libs so edits on disk always take effect.
for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

from pyrevit import forms, script

from pymep_config import (
    get_github_repo, get_github_token, get_downloads_folder,
    get_local_version,
)
import pymep_update as upd
from pymep_log import Logger

output = script.get_output()
log = Logger(output, "InstallUpdate")

repo = get_github_repo()
token = get_github_token()
downloads = get_downloads_folder()
local_ver = get_local_version()

log("### Install Update")
log("Repo: **{}**".format(repo))
log("Installed version: **{}**".format(local_ver or "(no version.txt)"))

if not os.path.isdir(downloads):
    forms.alert(
        "Downloads folder not found:\n{}\n\nSet the "
        "'update_downloads_folder' key in pyMEP_settings.json to the "
        "folder you want updates staged in.".format(downloads),
        exitscript=True)

label, zip_url = upd.resolve_latest(repo, token)
log("Latest on GitHub: **{}**".format(label))

if local_ver and local_ver == label:
    if forms.alert(
            "You already have the latest version ({}).\n\n"
            "Download and reinstall it anyway?".format(label),
            title="Up to date",
            options=["Reinstall", "Cancel"]) != "Reinstall":
        log("Already up to date - cancelled.")
        log.close()
        script.exit()

zip_path = upd.download_extension_zip(label, zip_url, repo=repo,
                                      token=token, downloads=downloads,
                                      log=log)
if zip_path is None:
    fallback = os.path.join(downloads, "pyMEP.extension.zip")
    if os.path.exists(fallback):
        fb_ts = datetime.datetime.fromtimestamp(
            os.path.getmtime(fallback)).strftime("%Y-%m-%d %H:%M")
        if forms.alert(
                "The download failed (full details in the output window).\n\n"
                "Downloads already contains a pyMEP.extension.zip from "
                "{}.\n\nInstall that one instead?".format(fb_ts),
                title="Download failed",
                options=["Install existing zip", "Cancel"]) == \
                "Install existing zip":
            zip_path = fallback
            label = "(existing zip, {})".format(fb_ts)
    if zip_path is None:
        log.close()
        forms.alert(
            "Download failed - nothing was changed.\n\nIf the repository "
            "is private, set the 'github_token' key in pyMEP_settings.json "
            "to a GitHub personal-access token with repo read access.",
            exitscript=True)

if forms.alert(
        "Install {} over the live extension?\n\nInstalled version: {}\n\n"
        "The previous version's folder is removed after a successful "
        "install - any version can be reinstalled from Settings > "
        "General > Downgrade / reinstall.".format(
            label, local_ver or "(no version.txt)"),
        title="Install Update",
        options=["Install", "Cancel"]) != "Install":
    log("Cancelled.")
    log.close()
    script.exit()

try:
    new_ver = upd.deploy_zip(zip_path, log=log)
except Exception as ex:
    import traceback
    log(traceback.format_exc())
    log.close()
    forms.alert("{}".format(ex), exitscript=True)

log("### Installed **{}**".format(new_ver or "(no version.txt in zip)"))

if forms.alert(
        "Installed {}.\n\nReload pyRevit now so the new version is live?"
        .format(new_ver or "the update"),
        title="Installed",
        options=["Reload pyRevit", "Later"]) == "Reload pyRevit":
    log.close()
    try:
        from pyrevit.loader import sessionmgr
        sessionmgr.reload_pyrevit()
    except Exception as ex:
        forms.alert(
            "Automatic reload failed ({}).\n\nReload manually: pyRevit tab "
            "> Reload.".format(ex))
else:
    log("Reload skipped - use pyRevit tab > Reload when ready.")
    log.close()
