# -*- coding: utf-8 -*-
"""Import Pipe Types - pick another Revit file and copy its pipe types
straight into this model.

The source .rvt is opened invisibly in the background (detached when
workshared - the real file is never touched), the picked pipe types are
copied across WITH their routing preferences, segments, schedules,
materials and fitting families (same mechanism as Transfer Project
Standards), and the source is closed without saving. Types already in
this model are kept, never overwritten or duplicated as 'name 2'.

Limit: Revit cannot open a file saved in a NEWER version than the one
running - that direction has no import path. All copy logic lives in
lib/pymep_pipetypes_copy.py; this file is UI + orchestration only.
"""

__title__  = "Import\nPipe Types"
__author__ = "Glent Group"

import os
import sys

# Force-reload pymep_* libs so edits on disk always take effect.
for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

from pyrevit import revit, forms, script

from pymep_pipetypes_copy import (
    open_source_document, copy_pipe_types, list_pipe_types,
)
from pymep_log import Logger

output = script.get_output()
log = Logger(output, "ImportPipeTypes")
doc = revit.doc

log("### Import Pipe Types from another Revit file")

# ---------------------------------------------------------------------------
# 1. Pick the source .rvt
# ---------------------------------------------------------------------------
rvt_path = forms.pick_file(
    file_ext="rvt", title="Pick the Revit file to import pipe types from")
if not rvt_path:
    forms.alert("No file picked.", exitscript=True)

try:
    if doc.PathName and \
            os.path.normcase(os.path.normpath(rvt_path)) == \
            os.path.normcase(os.path.normpath(doc.PathName)):
        forms.alert("That IS the active model - pick the file to import "
                    "FROM.", exitscript=True)
except Exception:
    pass

log("Source: **{}**".format(os.path.basename(rvt_path)))
log("Opening in the background (detached if workshared) ...")

try:
    src_doc = open_source_document(doc.Application, rvt_path)
except Exception as ex:
    log("Open failed: {}".format(ex))
    log.close()
    forms.alert(
        "Revit could not open that file:\n\n{}\n\n"
        "If it was saved in a NEWER Revit than this one, no import is "
        "possible - Revit cannot read files from later versions. Open "
        "this model in that Revit version (or recreate the types "
        "there) instead.".format(ex),
        exitscript=True)

# From here on the source doc MUST be closed, whatever happens.
try:
    # -----------------------------------------------------------------------
    # 2. Pick the pipe types (default: all)
    # -----------------------------------------------------------------------
    src_types = list_pipe_types(src_doc)
    if not src_types:
        forms.alert("'{}' has no pipe types.".format(
            os.path.basename(rvt_path)), exitscript=True)
    log("Found **{}** pipe type(s) in the source.".format(len(src_types)))

    choice = forms.alert(
        "Import which pipe types from '{}'?\n\n"
        "Each type comes in with its routing preferences, segments, "
        "schedules, materials and fitting families. Types already in "
        "this model are kept unchanged.".format(
            os.path.basename(rvt_path)),
        title="Import Pipe Types",
        options=["Import all {}".format(len(src_types)),
                 "Pick which types...", "Cancel"])
    if not choice or choice == "Cancel":
        script.exit()

    picked = [pt for _nm, pt in src_types]
    if choice.startswith("Pick"):
        class TypeOption(object):
            def __init__(self, nm, pt):
                self.name = nm
                self.pt = pt
        sel = forms.SelectFromList.show(
            [TypeOption(nm, pt) for nm, pt in src_types],
            title="Pick pipe types to import",
            button_name="Import these",
            multiselect=True,
            name_attr="name")
        if not sel:
            forms.alert("No types picked - nothing to import.",
                        exitscript=True)
        picked = [o.pt for o in sel]
    log("Importing **{}** pipe type(s) ...".format(len(picked)))

    # -----------------------------------------------------------------------
    # 3. Copy
    # -----------------------------------------------------------------------
    try:
        created, existed = copy_pipe_types(src_doc, doc, picked, log=log)
    except Exception as ex:
        import traceback
        log(traceback.format_exc())
        forms.alert("Import failed - nothing was changed:\n\n{}".format(ex),
                    exitscript=True)
finally:
    try:
        src_doc.Close(False)
        log("Source closed (not saved).")
    except Exception:
        pass

# ---------------------------------------------------------------------------
# 4. Summary
# ---------------------------------------------------------------------------
log("#### Summary")
log("- New pipe types: **{}**".format(len(created)))
log("- Already present (kept): **{}**".format(len(existed)))

forms.alert(
    "Imported {} new pipe type(s) from {}.\n"
    "Already present (kept): {}.\n\n"
    "Routing preferences, segments, schedules, materials and fitting "
    "families came along with them.".format(
        len(created), os.path.basename(rvt_path), len(existed)),
    title="Pipe types imported")
log.close()
