# -*- coding: utf-8 -*-
"""Family at Pipe Top - place a family instance on the top end of every
selected pipe, and optionally delete the pipe afterwards.

The inverse of Structure to Pipe: select the pipes that came in from a
Civil 3D conversion, pick the family type to sit on them (chamber,
gully, cover, node), and each pipe gets one instance at its higher end.
Tick the checkbox to have the pipe removed once its family is placed.
"""

__title__ = "Family at Pipe Top"
__author__ = "Glent Group"

import sys

for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

import os

from pyrevit import revit, forms, script

from pymep_config import load_settings, save_settings
from pymep_log import Logger
from pymep_revit import safe_name
from pymep_pipe_to_family import (
    place_at_tops, placement_summary, search_symbol_rows, selected_pipes,
    symbol_categories, symbol_families, symbol_rows, symbol_types_in,
)

output = script.get_output()
log = Logger(output, "PipeToFamily")
doc = revit.doc
uidoc = revit.uidoc

log("### Family at Pipe Top")

XAML_PATH = os.path.join(
    os.path.dirname(os.path.abspath(sys.modules["pymep_config"].__file__)),
    "pymep_pipe_to_family.xaml")

# ---------------------------------------------------------------------------
# 1. The selected pipes
# ---------------------------------------------------------------------------
pipes = selected_pipes(doc, uidoc)
if not pipes:
    forms.alert("Select one or more pipes first, then run Family at Pipe "
                "Top.", exitscript=True)
log("**{}** pipe(s) selected.".format(len(pipes)))

# ---------------------------------------------------------------------------
# 2. The family types this model can place
# ---------------------------------------------------------------------------
rows = symbol_rows(doc)
if not rows:
    forms.alert("This model has no loadable family types to place. Load a "
                "family first.", exitscript=True)

settings = load_settings()


# ---------------------------------------------------------------------------
# 3. Dialog: which family type, and what to do with the pipe
# ---------------------------------------------------------------------------
class TopFamilyWindow(forms.WPFWindow):

    def __init__(self):
        forms.WPFWindow.__init__(self, XAML_PATH)
        self.result = None
        self._shown = []
        self._loading = True
        self.TxtInfo.Text = "{} pipe(s) selected".format(len(pipes))
        self.ChkDelete.IsChecked = bool(
            settings.get("topfam_delete", False))

        self.CmbCat.Items.Clear()
        for c in symbol_categories(rows):
            self.CmbCat.Items.Add(c)
        want = settings.get("topfam_label")
        prev = None
        for r in rows:
            if r["label"] == want:
                prev = r
                break
        self._loading = False
        self.CmbCat.SelectedItem = prev["cat"] if prev else (
            self.CmbCat.Items[0] if self.CmbCat.Items.Count else None)
        if prev:
            self.CmbFam.SelectedItem = prev["fam"]
            for i, r in enumerate(self._shown):
                if r["label"] == prev["label"]:
                    self.CmbType.SelectedIndex = i
                    break

    # ---- cascade ----------------------------------------------------------
    def _fill_fams(self):
        cat = self.CmbCat.SelectedItem
        self.CmbFam.Items.Clear()
        if cat is None:
            return
        for f in symbol_families(rows, str(cat)):
            self.CmbFam.Items.Add(f)
        if self.CmbFam.Items.Count:
            self.CmbFam.SelectedIndex = 0

    def _fill_types(self):
        cat, fam = self.CmbCat.SelectedItem, self.CmbFam.SelectedItem
        self._show_rows(symbol_types_in(rows, str(cat), str(fam))
                        if (cat is not None and fam is not None) else [])

    def _show_rows(self, shown):
        self._shown = list(shown)
        self.CmbType.Items.Clear()
        for r in self._shown:
            self.CmbType.Items.Add(r["type"] if not self.TxtSearch.Text
                                   else r["label"])
        if self.CmbType.Items.Count:
            self.CmbType.SelectedIndex = 0

    def on_cat_changed(self, sender, args):
        if self._loading:
            return
        self._fill_fams()
        self._fill_types()

    def on_fam_changed(self, sender, args):
        if self._loading:
            return
        self._fill_types()

    def on_search(self, sender, args):
        if self._loading:
            return
        query = self.TxtSearch.Text
        hits = search_symbol_rows(rows, query)
        if hits:
            for c in (self.CmbCat, self.CmbFam):
                c.IsEnabled = False
            self._show_rows(hits)
            self.StatusText.Text = ""
        elif (query or "").strip():
            self._show_rows([])
            self.StatusText.Text = "Nothing matches '{}'.".format(query)
        else:
            for c in (self.CmbCat, self.CmbFam):
                c.IsEnabled = True
            self._fill_types()
            self.StatusText.Text = ""

    # ---- buttons ----------------------------------------------------------
    def on_place(self, sender, args):
        i = self.CmbType.SelectedIndex
        if i < 0 or i >= len(self._shown):
            self.StatusText.Text = "Pick a family type to place."
            return
        self.result = {"row": self._shown[i],
                       "delete": bool(self.ChkDelete.IsChecked)}
        self.Close()

    def on_cancel(self, sender, args):
        self.result = None
        self.Close()


win = TopFamilyWindow()
win.ShowDialog()
if win.result is None:
    log("Cancelled - nothing changed.")
    log.close()
    script.exit()

row = win.result["row"]
delete_pipes = win.result["delete"]
log("Family: **{}**{}".format(
    row["label"], " (pipes deleted afterwards)" if delete_pipes else ""))

settings["topfam_label"] = row["label"]
settings["topfam_delete"] = delete_pipes
try:
    save_settings(settings)
except Exception:
    pass

# ---------------------------------------------------------------------------
# 4. Place
# ---------------------------------------------------------------------------
try:
    res = place_at_tops(doc, pipes, row["id"], delete_pipes=delete_pipes,
                        log=log)
except Exception as ex:
    import traceback
    log(traceback.format_exc())
    forms.alert("Nothing was placed - the model is unchanged.\n\n"
                "{}".format(ex), title="Family at Pipe Top", exitscript=True)

log("#### Summary")
log("- Families placed: **{}**".format(res["placed"]))
if res["deleted"]:
    log("- Pipes deleted: **{}**".format(res["deleted"]))
if res["failed"]:
    log("- Pipes skipped: **{}**".format(res["failed"]))

forms.alert(placement_summary(res["placed"], res["deleted"], res["failed"]),
            title="Family at Pipe Top")
log.close()
