# -*- coding: utf-8 -*-
"""Import MEP Types - pick another Revit file and copy its types
straight into this model, choosing which ones from each category.

The source .rvt is opened invisibly in the background (detached when
workshared - the real file is never touched). Every importable type
category present in the source is offered - pipe types, piping system
types, pipe segments, duct types, duct system types, cable tray types,
conduit types - grouped in the picker so you can take exactly the ones
you want from each. Each type comes in WITH its dependents (routing
preferences, segments, schedules, materials, fitting families - same
mechanism as Transfer Project Standards); the source is closed without
saving. Types already in this model are kept, never overwritten or
duplicated as 'name 2'.

Limit: Revit cannot open a file saved in a NEWER version than the one
running - that direction has no import path. All copy logic lives in
lib/pymep_pipetypes_copy.py; this file is UI + orchestration only.
"""

__title__  = "Import\nMEP Types"
__author__ = "Glent Group"

import os
import sys

# Force-reload pymep_* libs so edits on disk always take effect.
for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

from pyrevit import revit, forms, script

from pymep_pipetypes_copy import (
    open_source_document, copy_types, list_types_by_category,
)
from pymep_log import Logger

output = script.get_output()
log = Logger(output, "ImportMEPTypes")
doc = revit.doc

log("### Import MEP Types from another Revit file")

# ---------------------------------------------------------------------------
# 1. Pick the source .rvt
# ---------------------------------------------------------------------------
rvt_path = forms.pick_file(
    file_ext="rvt", title="Pick the Revit file to import MEP types from")
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
report = None
try:
    # -----------------------------------------------------------------------
    # 2. List types by category, let the user choose per category
    # -----------------------------------------------------------------------
    src_types = list_types_by_category(src_doc)
    if not src_types:
        forms.alert("'{}' has no importable MEP types.".format(
            os.path.basename(rvt_path)), exitscript=True)

    cats = []
    for label, _nm, _el in src_types:
        if label not in cats:
            cats.append(label)
    log("Found **{}** type(s) across {} categor{}: {}.".format(
        len(src_types), len(cats), "y" if len(cats) == 1 else "ies",
        ", ".join("{} ({})".format(
            c, sum(1 for t in src_types if t[0] == c)) for c in cats)))

    choice = forms.alert(
        "Import types from '{}'.\n\n"
        "Pick which ones you want from each category, or take the lot. "
        "Every type comes in with its dependents; types already in this "
        "model are kept unchanged.".format(os.path.basename(rvt_path)),
        title="Import MEP Types",
        options=["Choose per category...",
                 "Import all {}".format(len(src_types)), "Cancel"])
    if not choice or choice == "Cancel":
        script.exit()

    picks = list(src_types)
    if choice.startswith("Choose"):
        class TypeOption(object):
            def __init__(self, tup):
                self.tup = tup
                self.name = tup[1]
        # grouped by category so the picker has a per-category switcher
        groups = {}
        for tup in src_types:
            groups.setdefault(tup[0], []).append(TypeOption(tup))
        for k in groups:
            groups[k].sort(key=lambda o: o.name.lower())
        sel = forms.SelectFromList.show(
            groups,
            title="Choose MEP types to import (switch category at the top)",
            button_name="Import selected",
            multiselect=True,
            name_attr="name",
            group_selector_title="Category")
        if not sel:
            forms.alert("Nothing selected - nothing to import.",
                        exitscript=True)
        picks = [o.tup for o in sel]

    log("Importing **{}** type(s) ...".format(len(picks)))

    # -----------------------------------------------------------------------
    # 3. Copy
    # -----------------------------------------------------------------------
    try:
        report = copy_types(src_doc, doc, picks, log=log)
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
total_new = sum(len(c) for c, _k in report.values()) if report else 0
total_kept = sum(len(k) for _c, k in report.values()) if report else 0
log("#### Summary")
lines = []
if report:
    for label in sorted(report.keys()):
        created, kept = report[label]
        log("- {}: **{}** new, {} already present".format(
            label, len(created), len(kept)))
        lines.append("{}: {} new, {} kept".format(
            label, len(created), len(kept)))

forms.alert(
    "Imported {} new type(s) from {} ({} already present, kept).\n\n{}\n\n"
    "Dependents (routing preferences, segments, schedules, materials, "
    "fitting families) came along with them.".format(
        total_new, os.path.basename(rvt_path), total_kept,
        "\n".join(lines) or "(nothing)"),
    title="MEP types imported")
log.close()
