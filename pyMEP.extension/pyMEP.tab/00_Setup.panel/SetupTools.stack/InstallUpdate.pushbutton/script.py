# -*- coding: utf-8 -*-
"""Install Update - deploy Downloads/pyMEP.extension.zip over this
extension, exactly like supersede_pyExtensions.py (in the repo root):

  1. Move the live pyMEP.extension folder to
     <extensions root>/00 - Superseded/pyMEP/pyMEP.extension_<timestamp>;
  2. Extract Downloads/pyMEP.extension.zip into its place (handles both a
     single top-level folder and loose files in the zip);
  3. Archive the zip next to the superseded copy;
  4. Offer to reload pyRevit so the new version is live.

If anything fails after the live folder was moved, the move is rolled
back, so you are never left without a working extension. If the folder
can't be moved at all (file locked by Revit), nothing is touched - close
Revit and run supersede_pyExtensions.py instead.
"""

__title__  = "Install Update"
__author__ = "Glent Group"

import datetime
import os
import shutil
import sys
import zipfile

# Force-reload pymep_* libs so edits on disk always take effect.
for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

from pyrevit import forms, script

from pymep_config import EXT_ROOT, get_downloads_folder, get_local_version
from pymep_log import Logger

output = script.get_output()
log = Logger(output, "InstallUpdate")

ext_dir = EXT_ROOT                                  # ...\pyMEP.extension
base = os.path.dirname(ext_dir)                     # pyRevit extensions root
source_name = os.path.basename(ext_dir)             # 'pyMEP.extension'
short = (source_name[:-len(".extension")]
         if source_name.endswith(".extension") else source_name)

downloads = get_downloads_folder()
zip_path = os.path.join(downloads, source_name + ".zip")

log("### Install Update")
log("Live extension: **{}**".format(ext_dir))
log("Looking for: **{}**".format(zip_path))

if not os.path.exists(zip_path):
    forms.alert(
        "No '{}.zip' in:\n{}\n\nRun Download Latest first (or drop an "
        "extension zip into Downloads).".format(source_name, downloads),
        exitscript=True)

zip_ts = datetime.datetime.fromtimestamp(
    os.path.getmtime(zip_path)).strftime("%Y-%m-%d %H:%M")
local_ver = get_local_version()

if forms.alert(
        "Install {}.zip (downloaded {}) over the live extension?\n\n"
        "Installed version: {}\n\nThe current folder is kept: it moves to\n"
        "00 - Superseded\\{}\\{}_<timestamp>".format(
            source_name, zip_ts, local_ver or "(no version.txt)",
            short, source_name),
        title="Install Update",
        options=["Install", "Cancel"]) != "Install":
    log("Cancelled.")
    log.close()
    script.exit()


def unique_destination(parent, stem, suffix=""):
    """Non-clashing path at parent/<stem><suffix>, appending _N if taken
    (same rule as supersede_pyExtensions.py)."""
    dest = os.path.join(parent, stem + suffix)
    counter = 1
    while os.path.exists(dest):
        dest = os.path.join(parent, "{}_{}{}".format(stem, counter, suffix))
        counter += 1
    return dest


ts = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
superseded_dir = os.path.join(base, "00 - Superseded", short)
try:
    if not os.path.exists(superseded_dir):
        os.makedirs(superseded_dir)
except Exception as ex:
    log("makedirs failed: {}".format(ex))
    log.close()
    forms.alert(
        "Couldn't create the superseded folder:\n{}\n\n{}\n\nNothing was "
        "changed.".format(superseded_dir, ex), exitscript=True)

# ---- 1. supersede the live folder (abort cleanly if Windows says no) ------
# os.rename, NOT shutil.move: superseded_dir is under the same parent, so
# this is an atomic same-volume rename - it either fully succeeds or does
# nothing, which is what makes the 'Nothing was changed' promise true.
# (shutil.move would fall back to copy+delete and could fail HALFWAY.)
moved_to = unique_destination(superseded_dir,
                              "{}_{}".format(source_name, ts))
try:
    os.rename(ext_dir, moved_to)
except Exception as ex:
    log("Move failed: {}".format(ex))
    log.close()
    forms.alert(
        "Couldn't move the live extension folder (a file in it is probably "
        "locked by Revit):\n\n{}\n\nNothing was changed. Close Revit and "
        "run supersede_pyExtensions.py instead.".format(ex),
        exitscript=True)
log("Superseded: {} -> {}".format(source_name, moved_to))

# ---- 2. extract the zip into place (roll back the move on ANY failure) ----
temp_dir = os.path.join(base, ".__extract_" + source_name)
try:
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)
    os.makedirs(temp_dir)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(temp_dir)

    extracted = [os.path.join(temp_dir, n) for n in os.listdir(temp_dir)]
    if len(extracted) == 1 and os.path.isdir(extracted[0]):
        # zip had a single top-level folder - move it into place
        os.rename(extracted[0], ext_dir)
        shutil.rmtree(temp_dir, ignore_errors=True)
    else:
        # zip had loose files - the temp dir itself becomes the extension
        os.rename(temp_dir, ext_dir)

    if not os.path.isdir(os.path.join(ext_dir, "pyMEP.tab")):
        raise RuntimeError(
            "the extracted zip has no pyMEP.tab folder - not a "
            "pyMEP.extension zip?")
except Exception as ex:
    shutil.rmtree(temp_dir, ignore_errors=True)
    try:
        if os.path.exists(ext_dir):
            shutil.rmtree(ext_dir)          # discard the bad extract
        os.rename(moved_to, ext_dir)        # put the old version back
        outcome = "The previous version was RESTORED."
    except Exception as ex2:
        outcome = ("Restoring the previous version ALSO failed ({}) - "
                   "recover it manually from:\n{}".format(ex2, moved_to))
    log("Install failed: {}".format(ex))
    log.close()
    forms.alert("Install failed:\n\n{}\n\n{}".format(ex, outcome),
                exitscript=True)
log("Extracted -> {}".format(ext_dir))

# ---- 3. archive the zip next to the superseded copy (non-fatal) -----------
try:
    archived = unique_destination(superseded_dir,
                                  "{}_{}".format(source_name, ts), ".zip")
    shutil.move(zip_path, archived)
    log("Archived zip: {} -> {}".format(os.path.basename(zip_path), archived))
except Exception as ex:
    log("Couldn't archive the zip (non-fatal): {}".format(ex))

new_ver = get_local_version()
log("### Installed **{}**".format(new_ver or "(no version.txt in zip)"))

# ---- 4. reload pyRevit -----------------------------------------------------
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
