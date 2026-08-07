# -*- coding: utf-8 -*-
"""Export Project Data - one sectioned dialog writes picked project
data to a version-agnostic JSON.

Sections (today): VIEW TEMPLATES and FILTERS. Each section has a
Select... button - the template picker filters by view family (floor
plan, 3D, section, ...) - and a tick deciding whether that section
goes to the file at all. The JSON crosses Revit versions, diffs
cleanly in git and can be hand-edited.
"""

__title__  = "Export Project\nData"
__author__ = "Glent Group"

import datetime
import io
import sys

for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

from pyrevit import revit, forms, script

from pymep_log import Logger
from pymep_project_data_ui import ProjectDataWindow
from pymep_vt_schema import dumps
from pymep_vt_serialize import export_document

import clr
clr.AddReference("RevitAPI")
from Autodesk.Revit.DB import (FilteredElementCollector,
                               ParameterFilterElement, View)

output = script.get_output()
log = Logger(output, "ExportProjectData")
doc = revit.doc

log("### Export Project Data")

templates = []
for v in FilteredElementCollector(doc).OfClass(View):
    try:
        if v.IsTemplate:
            templates.append(v)
    except Exception:
        continue
filters = sorted(
    [f for f in FilteredElementCollector(doc).OfClass(
        ParameterFilterElement)], key=lambda f: f.Name)

if not templates and not filters:
    log("No view templates or filters in this model.")
    log.close()
    forms.alert("This model has no view templates and no rule-based "
                "filters - nothing to export.", exitscript=True)

a_items = [(str(v.ViewType), v.Name, v) for v in templates]
b_items = [(None, f.Name, f) for f in filters]

win = ProjectDataWindow(
    "Export Project Data",
    "{} view template(s), {} filter(s) in this model - everything "
    "starts selected; refine with the Select buttons.".format(
        len(templates), len(filters)),
    "Export", a_items, b_items, "Sections to export")
win.ShowDialog()
if win.result is None:
    log("Cancelled - nothing exported.")
    log.close()
    script.exit()
opt = win.result

path = forms.save_file(file_ext="json",
                       default_name="project_data.json")
if not path:
    log("No file chosen - nothing exported.")
    log.close()
    script.exit()

views = opt["a"] if opt["a_on"] else []
extra = opt["b"] if opt["b_on"] else []
data, results = export_document(doc, views, extra,
                                include_referenced=opt["b_on"])
if not opt["b_on"] and views:
    log("Filters section UNTICKED - templates export without their "
        "filters; the import will note each missing one.")
data["exported"] = datetime.datetime.now().strftime(
    "%Y-%m-%dT%H:%M:%S")
text = dumps(data)
# IronPython 2.7: io text streams take unicode ONLY - a plain str
# write throws and leaves an EMPTY file ('No JSON object could be
# decoded' on import)
if not isinstance(text, type(u"")):
    text = text.decode("utf-8")
with io.open(path, "w", encoding="utf-8") as f:
    f.write(text)

log("Written: **{}**".format(path))
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
forms.alert("Exported {} template(s) and {} filter(s) to:\n{}".format(
    len(data["view_templates"]), len(data["filters"]), path),
    title="Project data exported")
