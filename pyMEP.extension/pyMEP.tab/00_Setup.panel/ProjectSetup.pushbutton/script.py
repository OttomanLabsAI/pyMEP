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
    fallback_map = None
    try:
        from pymep_config import get_dashboard_layer_workset_map
        fallback_map = get_dashboard_layer_workset_map()
    except Exception:
        fallback_map = None
    try:
        layers, worksets, layer_colors = setup_lib.read_model_export(
            cfg_path, fallback_workset_map=fallback_map)
    except ValueError as ex:
        forms.alert("{}".format(ex), exitscript=True)

    # one grid window: classifications pre-filled automatically from the
    # layer names, overridable per row before OK
    from System.Collections import ArrayList, Hashtable

    class ClassWindow(forms.WPFWindow):

        def __init__(self):
            forms.WPFWindow.__init__(
                self, os.path.join(_FOLDER, "assign_grid.xaml"))
            self.result = None
            self.CmbValue.Items.Clear()
            for c in setup_lib.PIPE_CLASSIFICATIONS:
                self.CmbValue.Items.Add(c)
            self.CmbValue.SelectedIndex = 0
            items = ArrayList()
            for lay in layers:
                row = Hashtable()
                row["layer"] = lay
                row["value"] = setup_lib.classify_layer(lay)
                items.Add(row)
            self.LstRows.ItemsSource = items

        def on_assign(self, sender, args):
            pick = self.CmbValue.SelectedItem
            if pick is None:
                return
            for row in self.LstRows.SelectedItems:
                row["value"] = str(pick)
            self.LstRows.Items.Refresh()

        def on_ok(self, sender, args):
            self.result = [(str(row["layer"]), str(row["value"]))
                           for row in self.LstRows.ItemsSource]
            self.Close()

        def on_cancel(self, sender, args):
            self.Close()

    cwin = ClassWindow()
    cwin.ShowDialog()
    if not cwin.result:
        script.exit()
    pairs = cwin.result
    output.print_md("#### Layer classifications")
    for lay, cls in pairs:
        output.print_md("- {}  ->  **{}**".format(lay, cls))
    config = setup_lib.config_from_layers(pairs, worksets,
                                          layer_colors=layer_colors)
    if not config.get("worksets"):
        forms.alert(
            "This export carries no workset map, and no saved layer -> "
            "workset map was found on this machine either - so NO "
            "worksets and NO per-workset view templates will be "
            "created (the layer systems still will).\n\nSet up the "
            "dashboard's Workset settings (Worksets button), Export "
            "model again, and re-run for the templates.",
            title="Project Setup - no worksets in export")
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
