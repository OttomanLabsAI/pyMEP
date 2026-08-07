# -*- coding: utf-8 -*-
"""Export Project Data - one sectioned dialog writes picked project
data to a version-agnostic JSON.

Sections: VIEW TEMPLATES, FILTERS, LEVELS, FILL PATTERNS and LINE
PATTERNS. Each section has a Select... button - the template picker
filters by view family, the fill pattern picker by Drafting / Model -
and a tick deciding whether that section goes to the file at all.
Exporting levels and patterns lets the import rebuild them first, so
view ranges and overrides land without degrading.
"""

__title__  = "Export Project\nData"
__author__ = "Glent Group"

import datetime
import io
import os
import sys

for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

from pyrevit import revit, forms, script

from pymep_config import load_settings, save_settings
from pymep_log import Logger
from pymep_project_data_ui import ProjectDataWindow
from pymep_vt_schema import dumps, family_label
from pymep_vt_serialize import (export_document,
                                line_style_subcategories)

import clr
clr.AddReference("RevitAPI")
from Autodesk.Revit.DB import (FilteredElementCollector,
                               FillPatternElement, Level,
                               LinePatternElement,
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
levels = sorted(
    [l for l in FilteredElementCollector(doc).OfClass(Level)],
    key=lambda l: l.Elevation)
fills = []
for fpe in FilteredElementCollector(doc).OfClass(FillPatternElement):
    try:
        pat = fpe.GetFillPattern()
        if pat.IsSolidFill:
            continue
        fills.append((str(pat.Target), "{} ({})".format(
            pat.Name, pat.Target), fpe))
    except Exception:
        continue
line_pats = []
for lpe in FilteredElementCollector(doc).OfClass(LinePatternElement):
    try:
        line_pats.append((None, lpe.Name, lpe))
    except Exception:
        continue
line_styles = [(None, s.Name, s)
               for s in line_style_subcategories(doc)]

if not (templates or filters or levels or fills or line_pats or
        line_styles):
    log("Nothing exportable in this model.")
    log.close()
    forms.alert("This model has nothing to export.", exitscript=True)

# which filters each template USES - drives the auto-include tick
refs = {}
for v in templates:
    names = []
    try:
        for fid in v.GetFilters():
            fel = doc.GetElement(fid)
            if isinstance(fel, ParameterFilterElement):
                names.append(fel.Name)
    except Exception:
        pass
    refs[v.Name] = names

sections = [
    {"key": "templates", "header": "View templates",
     "items": [(family_label(str(v.ViewType)), v.Name, v)
               for v in templates],
     "hint": "The picker filters by view family (Floor Plan, 3D, "
             "Section, ...).",
     "pick_title": "Select view templates - the dropdown filters by "
                   "view family"},
    {"key": "filters", "header": "Filters",
     "items": [(None, f.Name, f) for f in filters],
     "hint": "Rule-based filters. With the tick on, changing the "
             "template selection keeps their filters ticked "
             "automatically."},
    {"key": "levels", "header": "Levels",
     "items": [(None, "{}  ({:.3f} m)".format(
         l.Name, l.Elevation * 0.3048), l) for l in levels],
     "hint": "Names + elevations, so imported view ranges resolve "
             "instead of degrading."},
    {"key": "fill_patterns", "header": "Fill patterns",
     "items": fills,
     "hint": "Drafting and model fill patterns (the built-in solid "
             "fill exists everywhere). The picker filters by "
             "Drafting / Model."},
    {"key": "line_patterns", "header": "Line patterns",
     "items": line_pats,
     "hint": "Line patterns used by overrides (the built-in Solid "
             "exists everywhere)."},
    {"key": "line_styles", "header": "Line styles",
     "items": line_styles,
     "hint": "Lines subcategories: projection weight, color and line "
             "pattern by name."},
]

settings = load_settings()
log("Model scanned: {} template(s), {} filter(s), {} level(s), {} "
    "fill pattern(s), {} line pattern(s), {} line style(s).".format(
        len(templates), len(filters), len(levels), len(fills),
        len(line_pats), len(line_styles)))
log("Opening the dialog - if it is not in front, ALT-TAB or check "
    "the other monitor.")
try:
    win = ProjectDataWindow(
        "Export Project Data",
    "{} template(s), {} filter(s), {} level(s), {} fill pattern(s), "
    "{} line pattern(s), {} line style(s) - everything starts "
    "selected; refine with the Select buttons.".format(
        len(templates), len(filters), len(levels), len(fills),
        len(line_pats), len(line_styles)),
        "Export", sections, "Sections to export",
        auto_link={"from": "templates", "to": "filters", "refs": refs,
                   "text": "Automatically include the filters the "
                           "selected view templates use"},
        auto_default=settings.get("pd_auto_filters", True))
    win.ShowDialog()
except Exception as ex:
    import traceback
    log(traceback.format_exc())
    log.close()
    forms.alert("The Export Project Data dialog FAILED to open:\n\n"
                "{}\n\nThe full traceback is in the pyMEP "
                "report.".format(ex), exitscript=True)
log("Dialog closed.")
if win.result is None:
    log("Cancelled - nothing exported.")
    log.close()
    script.exit()
opt = win.result
sec = opt["sections"]
settings["pd_auto_filters"] = opt.get("auto", True)
try:
    save_settings(settings)
except Exception:
    pass

path = forms.save_file(file_ext="json",
                       default_name="project_data.json")
if not path:
    log("No file chosen - nothing exported.")
    log.close()
    script.exit()


def _picked(key):
    d = sec.get(key) or {}
    return d.get("picked") or [] if d.get("on") else []


views = _picked("templates")
try:
    data, results = export_document(
        doc, views, _picked("filters"),
        include_referenced=bool((sec.get("filters") or {}).get("on"))
        and opt.get("auto", True),
        levels=_picked("levels"),
        fill_patterns=_picked("fill_patterns"),
        line_patterns=_picked("line_patterns"),
        line_styles=_picked("line_styles"))
    data["exported"] = datetime.datetime.now().strftime(
        "%Y-%m-%dT%H:%M:%S")
    try:
        with io.open(os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(
                    sys.modules["pymep_config"].__file__))),
                "version.txt"), "r", encoding="utf-8") as vf:
            data["pymep_version"] = vf.read().strip()
    except Exception:
        pass
    text = dumps(data)
    # IronPython 2.7: io text streams take unicode ONLY - a plain str
    # write throws and leaves an EMPTY file
    if not isinstance(text, type(u"")):
        text = text.decode("utf-8")
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(text)
except Exception as ex:
    import traceback
    log(traceback.format_exc())
    log.close()
    forms.alert("Export FAILED - the file was not written:\n\n{}"
                "\n\nThe full traceback is in the pyMEP "
                "report.".format(ex), exitscript=True)

size = 0
try:
    size = os.path.getsize(path)
except Exception:
    pass
if not size:
    log("! the written file is EMPTY - export failed")
    log.close()
    forms.alert("The written file came out EMPTY - export failed. "
                "See the pyMEP report.", exitscript=True)

log("Written: **{}** (**{:,}** bytes)".format(path, size))

# templates referencing filters NOT in this file will degrade on
# import - say so NOW, at the source, where it can be fixed
in_file = set(f.get("name") for f in data["filters"])
missing_refs = set()
for t in data["view_templates"]:
    for row in t.get("filters") or []:
        if row.get("name") and row["name"] not in in_file:
            missing_refs.add(row["name"])
if missing_refs:
    log("! **{}** filter(s) referenced by the exported templates are "
        "NOT in this file - the import will flag every one. "
        "Re-export with the auto tick ON (or Tick EVERYTHING) to "
        "include them: {}".format(
            len(missing_refs),
            ", ".join(sorted(missing_refs)[:10]) +
            (" ..." if len(missing_refs) > 10 else "")))
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
    "Exported {} template(s), {} filter(s), {} level(s), {} fill "
    "pattern(s), {} line pattern(s), {} line style(s) "
    "({:,} bytes) to:\n{}{}".format(
        len(data["view_templates"]), len(data["filters"]),
        len(data["levels"]), len(data["fill_patterns"]),
        len(data["line_patterns"]), len(data["line_styles"]),
        size, path,
        "\n\nWARNING: {} filter(s) the templates use are NOT in "
        "this file - the import will flag them. Re-export with the "
        "auto tick ON to include them.".format(len(missing_refs))
        if missing_refs else ""),
    title="Project data exported")
