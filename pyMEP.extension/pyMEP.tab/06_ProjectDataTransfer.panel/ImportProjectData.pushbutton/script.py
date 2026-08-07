# -*- coding: utf-8 -*-
"""Import Project Data - pick a JSON written by Export Project Data;
one sectioned dialog chooses what comes in.

Sections (today): VIEW TEMPLATES and FILTERS. Each section has a
Select... button - the template picker filters by view family - a
tick deciding whether the section imports at all, and the dialog
carries the update-or-skip choice for existing same-name items.
Filters import first, then templates; same-name items are UPDATED IN
PLACE so views keep their template assignment. Every anomaly lands in
the summary table with a reason; importing the same file twice is
idempotent.
"""

__title__  = "Import Project\nData"
__author__ = "Glent Group"

import io
import sys

for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

from pyrevit import revit, forms, script

from pymep_log import Logger
from pymep_project_data_ui import ProjectDataWindow
from pymep_vt_schema import filters_used_by, loads, validate_document
from pymep_vt_deserialize import (filter_ids_by_name, import_filter,
                                  import_template)

import clr
clr.AddReference("RevitAPI")
from Autodesk.Revit.DB import Transaction, TransactionGroup

output = script.get_output()
log = Logger(output, "ImportProjectData")
doc = revit.doc

log("### Import Project Data")

path = forms.pick_file(file_ext="json")
if not path:
    log("No file picked - nothing changed.")
    log.close()
    script.exit()

try:
    with io.open(path, "r", encoding="utf-8") as f:
        text = f.read()
    if not text.strip():
        raise ValueError(
            "the file is EMPTY - its export failed before writing "
            "(re-export with pyMEP v1.145.0 or newer)")
    data = loads(text)
except Exception as ex:
    log("! could not read the file: {}".format(ex))
    log.close()
    forms.alert("Could not read that file as JSON:\n\n{}".format(ex),
                exitscript=True)

problems = validate_document(data)
for p in problems:
    log("! {}".format(p))
file_templates = data.get("view_templates") or []
file_filters = data.get("filters") or []
if problems and not (file_templates or file_filters):
    log.close()
    forms.alert("That file is not a project-data export:\n\n{}".format(
        "\n".join(problems)), exitscript=True)
if not (file_templates or file_filters):
    log("The file holds no templates or filters.")
    log.close()
    forms.alert("Nothing to import in that file.", exitscript=True)
log("File: **{}** (exported from Revit {} on {})".format(
    path, data.get("revit_version") or "?", data.get("exported") or "?"))

a_items = [(t.get("view_family") or "Other", t.get("name") or "?", t)
           for t in file_templates]
b_items = [(None, f.get("name") or "?", f) for f in file_filters]

win = ProjectDataWindow(
    "Import Project Data",
    "In the file: {} view template(s), {} filter(s) - everything "
    "starts selected; refine with the Select buttons.".format(
        len(file_templates), len(file_filters)),
    "Import", a_items, b_items, "Sections to import",
    show_clash=True)
win.ShowDialog()
if win.result is None:
    log("Cancelled - nothing changed.")
    log.close()
    script.exit()
opt = win.result
update_existing = (opt["clash"] == "update")
log("Existing same-name items: **{}**.".format(
    "update in place" if update_existing else "skip"))

# filters first: the picked ones, plus every filter a picked template
# references (they must exist before the template attaches them)
by_name = dict((f.get("name"), f) for f in file_filters)
want_filters = {}
if opt["b_on"]:
    for f in opt["b"]:
        want_filters[f.get("name")] = f
if opt["a_on"]:
    for t in opt["a"]:
        for fname in filters_used_by(t):
            if fname in by_name and fname not in want_filters:
                want_filters[fname] = by_name[fname]

results = []
tg = TransactionGroup(doc, "Import Project Data")
tg.Start()
try:
    if want_filters:
        t = Transaction(doc, "Import filters")
        t.Start()
        for fname in sorted(want_filters):
            results.append(import_filter(doc, want_filters[fname],
                                         update_existing))
        t.Commit()
    if opt["a_on"] and opt["a"]:
        lookup = filter_ids_by_name(doc)
        t = Transaction(doc, "Import view templates")
        t.Start()
        for tdict in opt["a"]:
            results.append(import_template(doc, tdict, lookup,
                                           update_existing))
        t.Commit()
    tg.Assimilate()
except Exception:
    try:
        tg.RollBack()
    except Exception:
        pass
    raise

log("#### Summary")
log("| item | kind | status | notes |")
log("|---|---|---|---|")
for r in results:
    log("| {} | {} | **{}** | {} |".format(
        r["item"], r["kind"], r["status"], r["reason"] or "-"))
counts = {}
for r in results:
    counts[r["status"]] = counts.get(r["status"], 0) + 1
log("- " + ", ".join("{}: **{}**".format(k, v)
                     for k, v in sorted(counts.items())))
log.close()
forms.alert(
    "Import finished:\n" +
    "\n".join("  {}: {}".format(k, v)
              for k, v in sorted(counts.items())) +
    "\n\nDetails are in the pyMEP report.",
    title="Project data imported")
