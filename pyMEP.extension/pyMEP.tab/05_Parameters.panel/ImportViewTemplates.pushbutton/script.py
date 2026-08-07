# -*- coding: utf-8 -*-
"""Import View Templates - pick a JSON written by Export View
Templates, choose which templates / filters to bring in, and recreate
or update them in the active model.

Filters import first, then templates. Same-name items are UPDATED IN
PLACE so views keep their template assignment and templates keep
their filters - never delete-and-recreate. Every anomaly (missing
pattern, unmatched level, unknown parameter) lands in the summary
table as skipped or degraded with a reason; nothing raises at you.
"""

__title__  = "Import View\nTemplates"
__author__ = "Glent Group"

import io
import sys

for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

from pyrevit import revit, forms, script

from pymep_log import Logger
from pymep_vt_schema import filters_used_by, loads, validate_document
from pymep_vt_deserialize import (filter_ids_by_name, import_filter,
                                  import_template)

import clr
clr.AddReference("RevitAPI")
from Autodesk.Revit.DB import Transaction, TransactionGroup

output = script.get_output()
log = Logger(output, "ImportViewTemplates")
doc = revit.doc

log("### Import View Templates")

path = forms.pick_file(file_ext="json")
if not path:
    log("No file picked - nothing changed.")
    log.close()
    script.exit()

try:
    with io.open(path, "r", encoding="utf-8") as f:
        data = loads(f.read())
except Exception as ex:
    log("! could not read the file: {}".format(ex))
    log.close()
    forms.alert("Could not read that file as JSON:\n\n{}".format(ex),
                exitscript=True)

problems = validate_document(data)
for p in problems:
    log("! {}".format(p))
if problems and not (data.get("filters") or data.get("view_templates")):
    log.close()
    forms.alert("That file is not a view-template export:\n\n{}".format(
        "\n".join(problems)), exitscript=True)
log("File: **{}** (exported from Revit {} on {})".format(
    path, data.get("revit_version") or "?", data.get("exported") or "?"))


class _Pick(object):
    def __init__(self, label, kind, item):
        self.name = label
        self.kind = kind
        self.item = item


groups = {}
t_items = [_Pick(t.get("name") or "?", "template", t)
           for t in data.get("view_templates") or []]
f_items = [_Pick(f.get("name") or "?", "filter", f)
           for f in data.get("filters") or []]
if t_items:
    groups["View templates"] = sorted(t_items, key=lambda p: p.name)
if f_items:
    groups["Filters"] = sorted(f_items, key=lambda p: p.name)
if not groups:
    log("The file holds no templates or filters.")
    log.close()
    forms.alert("Nothing to import in that file.", exitscript=True)

picked = forms.SelectFromList.show(
    groups, title="Import View Templates - pick what to bring in",
    multiselect=True, button_name="Import")
if not picked:
    log("Nothing picked - nothing changed.")
    log.close()
    script.exit()

clash = forms.CommandSwitchWindow.show(
    ["Update existing", "Skip existing"],
    message="When a same-name filter / template already exists:")
if not clash:
    log("Cancelled - nothing changed.")
    log.close()
    script.exit()
update_existing = (clash == "Update existing")
log("Existing same-name items: **{}**.".format(clash.lower()))

# filters first: the ones picked, plus every filter a picked template
# references (they must exist before the template attaches them)
by_name = dict((f.get("name"), f) for f in data.get("filters") or [])
want_filters = {}
for p in picked:
    if p.kind == "filter":
        want_filters[p.name] = p.item
for p in picked:
    if p.kind == "template":
        for fname in filters_used_by(p.item):
            if fname in by_name and fname not in want_filters:
                want_filters[fname] = by_name[fname]

results = []
tg = TransactionGroup(doc, "Import View Templates")
tg.Start()
try:
    t = Transaction(doc, "Import filters")
    t.Start()
    for fname in sorted(want_filters):
        results.append(import_filter(doc, want_filters[fname],
                                     update_existing))
    t.Commit()

    lookup = filter_ids_by_name(doc)
    t = Transaction(doc, "Import view templates")
    t.Start()
    for p in picked:
        if p.kind == "template":
            results.append(import_template(doc, p.item, lookup,
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
    title="View templates imported")
