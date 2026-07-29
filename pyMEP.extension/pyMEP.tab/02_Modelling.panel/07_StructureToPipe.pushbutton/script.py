# -*- coding: utf-8 -*-
"""Structure to Pipe - replace a cylinder structure (a Generic Cylinder
Plumbing Fixture carrying DIA + H) with a real vertical Revit pipe of the
same diameter and length, in the same place and system, and delete the
original.

Select one or more such cylinders and run it. Each becomes a pipe of its
DIA bore, H long, standing on the cylinder's base at its exact XY, taking
the cylinder's System Type (and Mark / Comments). A one-off conversion
tool for models where vertical risers were placed as placeholder
cylinders.
"""

__title__  = "Structure\nto Pipe"
__author__ = "Glent Group"

import sys

for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

from pyrevit import revit, forms, script

from pymep_replace_structure import (
    read_cylinder, resolve_pipe_type, replace_with_pipe,
)
from pymep_revit import ft2mm, safe_name
from pymep_log import Logger

import clr
clr.AddReference("RevitAPI")
from Autodesk.Revit.DB import FamilyInstance

output = script.get_output()
log = Logger(output, "StructureToPipe")
doc = revit.doc
uidoc = revit.uidoc

log("### Structure to Pipe")

# ---------------------------------------------------------------------------
# 1. Gather selected family instances that carry DIA + H
# ---------------------------------------------------------------------------
candidates = []
skipped = []
for eid in uidoc.Selection.GetElementIds():
    el = doc.GetElement(eid)
    if not isinstance(el, FamilyInstance):
        continue
    info, reason = read_cylinder(el)
    if info is not None:
        # capture the name now - it is deleted during the replace
        candidates.append((el, info, safe_name(el)))
    else:
        skipped.append(reason)

if not candidates:
    forms.alert(
        "Select one or more cylinder structures (family instances with "
        "DIA and H parameters) first.\n\n{}".format(
            "\n".join(skipped[:5]) if skipped else ""),
        exitscript=True)

log("**{}** cylinder(s) to convert; {} selected element(s) skipped."
    .format(len(candidates), len(skipped)))

# ---------------------------------------------------------------------------
# 2. Resolve a pipe type to build with (Settings default, else first)
# ---------------------------------------------------------------------------
try:
    from pymep_config import get_pipe_type_name
    pref = get_pipe_type_name()
except Exception:
    pref = None
pipe_type = resolve_pipe_type(doc, pref)
if pipe_type is None:
    forms.alert("This model has no pipe types to build with. Load or "
                "create a pipe type first.", exitscript=True)
log("Pipe type: **{}**".format(safe_name(pipe_type)))

# ---------------------------------------------------------------------------
# 3. Confirm
# ---------------------------------------------------------------------------
msg = "Replace {} cylinder(s) with pipes?\n\n".format(len(candidates))
for el, info, name in candidates[:8]:
    msg += "  {}: {:.0f} mm dia x {:.0f} mm long\n".format(
        name, ft2mm(info["dia_ft"]), ft2mm(info["height_ft"]))
if len(candidates) > 8:
    msg += "  ... and {} more\n".format(len(candidates) - 8)
msg += "\nEach original cylinder is deleted."
if forms.alert(msg, title="Structure to Pipe",
               options=["Replace", "Cancel"]) != "Replace":
    forms.alert("Cancelled - nothing changed.", exitscript=True)

# ---------------------------------------------------------------------------
# 4. Replace each (each in its own transaction)
# ---------------------------------------------------------------------------
done = 0
failed = 0
for el, info, name in candidates:
    try:
        replace_with_pipe(doc, el, pipe_type, log=log)
        done += 1
    except Exception as ex:
        failed += 1
        import traceback
        log(traceback.format_exc())
        log("  ! {} not replaced: {}".format(name, ex))

log("#### Summary")
log("- Cylinders replaced with pipes: **{}**".format(done))
if failed:
    log("- Failed (left untouched): **{}**".format(failed))

forms.alert(
    "Replaced {} cylinder(s) with pipes.{}".format(
        done,
        "\n{} failed - see the report.".format(failed) if failed else ""),
    title="Structure to Pipe")
log.close()
