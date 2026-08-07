# -*- coding: utf-8 -*-
"""Import Project Data - pick a JSON written by Export Project Data;
one sectioned dialog chooses what comes in.

Sections: VIEW TEMPLATES, FILTERS, LEVELS, FILL PATTERNS and LINE
PATTERNS. Import order fixes the degrade causes: patterns first, then
levels, then filters, then templates - so overrides and view ranges
resolve against what was just brought in. Same-name items are UPDATED
IN PLACE (views keep their template assignment) or skipped, your
choice; every anomaly lands in the summary table with a reason;
importing the same file twice is idempotent.
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
from pymep_vt_schema import (family_label, filters_used_by, loads,
                             validate_document)
from pymep_vt_deserialize import (filter_ids_by_name, import_filter,
                                  import_fill_pattern, import_level,
                                  import_line_pattern,
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
file_levels = data.get("levels") or []
file_fills = data.get("fill_patterns") or []
file_lines = data.get("line_patterns") or []
have_any = (file_templates or file_filters or file_levels or
            file_fills or file_lines)
if problems and not have_any:
    log.close()
    forms.alert("That file is not a project-data export:\n\n{}".format(
        "\n".join(problems)), exitscript=True)
if not have_any:
    log("The file holds nothing to import.")
    log.close()
    forms.alert("Nothing to import in that file.", exitscript=True)
log("File: **{}** (exported from Revit {} on {}, pyMEP {})".format(
    path, data.get("revit_version") or "?", data.get("exported") or "?",
    data.get("pymep_version") or "?"))

sections = [
    {"key": "templates", "header": "View templates",
     "items": [(family_label(t.get("view_family")),
                t.get("name") or "?", t) for t in file_templates],
     "hint": "The picker filters by view family.",
     "pick_title": "Select view templates - the dropdown filters by "
                   "view family"},
    {"key": "filters", "header": "Filters",
     "items": [(None, f.get("name") or "?", f) for f in file_filters],
     "hint": "Filters used by the picked templates come in with them "
             "automatically."},
    {"key": "levels", "header": "Levels",
     "items": [(None, "{}  ({:.3f} m)".format(
         l.get("name") or "?",
         float(l.get("elevation_ft") or 0.0) * 0.3048), l)
         for l in file_levels],
     "hint": "With update chosen, an existing level MOVES to the "
             "file's elevation - everything hosted on it moves too."},
    {"key": "fill_patterns", "header": "Fill patterns",
     "items": [(f.get("target") or "Drafting", "{} ({})".format(
         f.get("name") or "?", f.get("target") or "?"), f)
         for f in file_fills],
     "hint": None},
    {"key": "line_patterns", "header": "Line patterns",
     "items": [(None, l.get("name") or "?", l) for l in file_lines],
     "hint": None},
]

win = ProjectDataWindow(
    "Import Project Data",
    "In the file: {} template(s), {} filter(s), {} level(s), {} fill "
    "pattern(s), {} line pattern(s) - everything starts selected; "
    "refine with the Select buttons.".format(
        len(file_templates), len(file_filters), len(file_levels),
        len(file_fills), len(file_lines)),
    "Import", sections, "Sections to import", show_clash=True)
win.ShowDialog()
if win.result is None:
    log("Cancelled - nothing changed.")
    log.close()
    script.exit()
opt = win.result
sec = opt["sections"]
update_existing = (opt["clash"] == "update")
log("Existing same-name items: **{}**.".format(
    "update in place" if update_existing else "skip"))


def _picked(key):
    d = sec.get(key) or {}
    return d.get("picked") or [] if d.get("on") else []


# filters: the picked ones, plus every filter a picked template
# references (they must exist before the template attaches them)
by_name = dict((f.get("name"), f) for f in file_filters)
want_filters = {}
for f in _picked("filters"):
    want_filters[f.get("name")] = f
for t in _picked("templates"):
    for fname in filters_used_by(t):
        if fname in by_name and fname not in want_filters:
            want_filters[fname] = by_name[fname]

results = []
tg = TransactionGroup(doc, "Import Project Data")
tg.Start()
try:
    fills_in = _picked("fill_patterns")
    lines_in = _picked("line_patterns")
    if fills_in or lines_in:
        t = Transaction(doc, "Import patterns")
        t.Start()
        for d in fills_in:
            results.append(import_fill_pattern(doc, d,
                                               update_existing))
        for d in lines_in:
            results.append(import_line_pattern(doc, d,
                                               update_existing))
        t.Commit()
    levels_in = _picked("levels")
    if levels_in:
        t = Transaction(doc, "Import levels")
        t.Start()
        for d in levels_in:
            results.append(import_level(doc, d, update_existing))
        t.Commit()
    if want_filters:
        t = Transaction(doc, "Import filters")
        t.Start()
        for fname in sorted(want_filters):
            results.append(import_filter(doc, want_filters[fname],
                                         update_existing))
        t.Commit()
    templates_in = _picked("templates")
    if templates_in:
        lookup = filter_ids_by_name(doc)
        t = Transaction(doc, "Import view templates")
        t.Start()
        for tdict in templates_in:
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
