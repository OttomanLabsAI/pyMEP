# -*- coding: utf-8 -*-
"""Project Setup - set up an MEP model from a JSON config, in one run:

  1. Worksets
  2. Piping system types (donor-duplicated per classification)
  3. View filters
  4. View templates (filters + overrides + workset visibility)

Everything project-specific lives in the config (configs/default.json
next to this script, or browse to a per-project one). This file is UI +
orchestration only - all stage logic is in setup_lib.py.
"""

__title__  = "Project\nSetup"
__author__ = "Glent Group"

import os
import sys

_FOLDER = os.path.dirname(__file__)
if _FOLDER not in sys.path:
    sys.path.append(_FOLDER)
if "setup_lib" in sys.modules:
    del sys.modules["setup_lib"]

from pyrevit import revit, DB, forms, script

import setup_lib

output = script.get_output()
doc = revit.doc

DEFAULT_CONFIG = os.path.join(_FOLDER, "configs", "default.json")

# 1. config selection -------------------------------------------------------
choice = forms.alert(
    "Set up this model from what?\n\n"
    "- A dashboard MODEL export: one piping system per layer (named "
    "exactly like the layer), the worksets from its workset map, and "
    "one isolation view template per workset (named after the workset, "
    "only that workset visible).\n"
    "- A JSON config (the bundled example default.json, or browse to a "
    "per-project file).",
    title="Project Setup",
    options=["Build from a dashboard MODEL export...",
             "Use the bundled default.json (example values)",
             "Browse for a project config...", "Cancel"])
if not choice or choice == "Cancel":
    script.exit()

if choice.startswith("Build"):
    cfg_path = forms.pick_file(
        file_ext="json", title="Pick a dashboard MODEL export (.json)")
    if not cfg_path:
        forms.alert("No export picked.", exitscript=True)
    cls_pick = forms.SelectFromList.show(
        setup_lib.PIPE_CLASSIFICATIONS,
        title="System classification for the layer systems (a donor "
              "type of this classification must exist in the model)",
        button_name="Use this classification", multiselect=False)
    if not cls_pick:
        forms.alert("No classification picked.", exitscript=True)
    try:
        config = setup_lib.config_from_model_export(cfg_path, cls_pick)
    except ValueError as ex:
        forms.alert("{}".format(ex), exitscript=True)
else:
    if choice.startswith("Browse"):
        cfg_path = forms.pick_file(
            file_ext="json", title="Pick the project setup config (.json)")
        if not cfg_path:
            forms.alert("No config picked.", exitscript=True)
    else:
        cfg_path = DEFAULT_CONFIG
    try:
        config = setup_lib.load_config(cfg_path)
    except ValueError as ex:
        forms.alert("{}".format(ex), exitscript=True)

# 2. stage selection (all pre-selected) -------------------------------------
stage_names = [nm for nm, _fn in setup_lib.STAGES]
picked = forms.SelectFromList.show(
    [forms.TemplateListItem(nm, checked=True) for nm in stage_names],
    title="Project Setup - run which stages?",
    button_name="Run selected stages", multiselect=True)
if not picked:
    forms.alert("No stages selected.", exitscript=True)
picked = set(picked)

# 3. run in the fixed order (filters may reference systems; templates
#    reference filters and worksets) ----------------------------------------
results = []
tg = DB.TransactionGroup(doc, "Project Setup")
tg.Start()
try:
    for nm, fn in setup_lib.STAGES:
        if nm in picked:
            fn(doc, config, results)
    tg.Assimilate()
except Exception as ex:
    import traceback
    try:
        tg.RollBack()
    except Exception:
        pass
    output.print_md("```\n{}\n```".format(traceback.format_exc()))
    forms.alert("Project Setup stopped and rolled back:\n\n{}\n\nThe "
                "full traceback is in the output window.".format(ex),
                exitscript=True)

# 4. report -----------------------------------------------------------------
output.print_md("### Project Setup - {}".format(os.path.basename(cfg_path)))
if results:
    output.print_table(
        table_data=[[r[0], r[1], r[2], r[3]] for r in results],
        columns=["Stage", "Item", "Status", "Detail"])
counts = {}
for r in results:
    counts[r[2]] = counts.get(r[2], 0) + 1
summary = "   ".join("{}: {}".format(k, counts[k]) for k in sorted(counts))
output.print_md("**{}**".format(summary or "nothing to do"))

n_failed = counts.get(setup_lib.FAILED, 0)
forms.alert("Project Setup finished.\n\n{}\n\nThe full report is in the "
            "output window.".format(summary or "Nothing to do."),
            title="Project Setup",
            warn_icon=n_failed > 0)
