# -*- coding: utf-8 -*-
"""Export View Templates - pick view templates (plus optional
standalone filters) and write them to one version-agnostic JSON.

The JSON crosses Revit versions (export from 2025, import into 2022),
diffs cleanly in git and can be hand-edited - same philosophy as the
pipe-sizes JSON. Filters referenced by the picked templates ride along
automatically; selection filters cannot cross models and are reported
skipped.
"""

__title__  = "Export View\nTemplates"
__author__ = "Glent Group"

import datetime
import io
import sys

for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

from pyrevit import revit, forms, script

from pymep_log import Logger
from pymep_vt_schema import dumps
from pymep_vt_serialize import export_document

import clr
clr.AddReference("RevitAPI")
from Autodesk.Revit.DB import (FilteredElementCollector,
                               ParameterFilterElement, View)

output = script.get_output()
log = Logger(output, "ExportViewTemplates")
doc = revit.doc

log("### Export View Templates")


class _Pick(object):
    def __init__(self, label, item):
        self.name = label
        self.item = item


templates = []
for v in FilteredElementCollector(doc).OfClass(View):
    try:
        if v.IsTemplate:
            templates.append(v)
    except Exception:
        continue
if not templates:
    log("No view templates in this model - nothing to export.")
    log.close()
    forms.alert("This model has no view templates.", exitscript=True)

picked_t = forms.SelectFromList.show(
    sorted([_Pick("{}  ({})".format(v.Name, v.ViewType), v)
            for v in templates], key=lambda p: p.name),
    title="Export View Templates - pick the templates",
    multiselect=True, button_name="Next")
if not picked_t:
    log("Nothing picked - nothing exported.")
    log.close()
    script.exit()
views = [p.item for p in picked_t]

filters = sorted(
    [f for f in FilteredElementCollector(doc).OfClass(
        ParameterFilterElement)], key=lambda f: f.Name)
extra = []
if filters:
    picked_f = forms.SelectFromList.show(
        [_Pick(f.Name, f) for f in filters],
        title="Standalone filters to ALSO export (optional) - filters "
              "used by the picked templates are included automatically",
        multiselect=True, button_name="Export")
    extra = [p.item for p in (picked_f or [])]

path = forms.save_file(file_ext="json",
                       default_name="view_templates.json")
if not path:
    log("No file chosen - nothing exported.")
    log.close()
    script.exit()

data, results = export_document(doc, views, extra)
data["exported"] = datetime.datetime.now().strftime(
    "%Y-%m-%dT%H:%M:%S")
with io.open(path, "w", encoding="utf-8") as f:
    f.write(dumps(data))

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
    title="View templates exported")
