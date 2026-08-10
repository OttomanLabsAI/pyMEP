# -*- coding: utf-8 -*-
"""Fence Configurations - the list of named set-ups used by Place
New Fence and Update Fence.

The main window is the LIST: every configuration with its values,
plus Add new / Edit / Remove (double-click a row to edit). Add new
and Edit open the configuration EDITOR - name, spacing, rotation,
endpoints, and the post + foundation families, each behind a text
search (posts from Generic Models / Columns / Structural Columns,
foundations from Structural Foundations; '(none)' allowed on both).

Saved in pyMEP settings, so they survive updates; Update Fence
re-reads them by name, so an edit here re-spaces / re-rotates /
re-posts / re-founds its fences on the next update.
"""

__title__  = "Fence\nConfigs"
__author__ = "Glent Group"

import os
import sys

for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

from pyrevit import revit, forms, script

from pymep_config import load_settings, save_settings
import pymep_fence as F
import pymep_fence_revit as FR

doc = revit.doc

_LIB = os.path.dirname(os.path.abspath(
    sys.modules["pymep_config"].__file__))
XAML_LIST = os.path.join(_LIB, "pymep_fence_configs.xaml")
XAML_EDIT = os.path.join(_LIB, "pymep_fence_config_edit.xaml")

NONE_LABEL = "(none)"


def _row_text(name, cfg):
    return (u"{}  —  {:g} mm, ends {}, rot {:+g}°  |  post: {}  |  "
            u"fnd: {}".format(
                name, cfg["spacing_mm"],
                "ON" if cfg["endpoints"] else "off",
                cfg["rotation_deg"],
                cfg["post"] or "none",
                cfg["foundation"] or "none"))


class ConfigEditWindow(forms.WPFWindow):
    """The editor: one configuration's values. result = {"name",
    "spacing", "endpoints", "rotation", "post", "foundation"} or
    None on cancel - the caller persists."""

    def __init__(self, title, name, cfg, post_labels, found_labels):
        forms.WPFWindow.__init__(self, XAML_EDIT)
        self.result = None
        self.post_labels = post_labels
        self.found_labels = found_labels
        self.Title = title
        self.TxtTitle.Text = title
        self.TxtName.Text = name
        self.TxtSpacing.Text = "{:g}".format(cfg["spacing_mm"])
        self.TxtRotation.Text = "{:g}".format(cfg["rotation_deg"])
        self.ChkEnds.IsChecked = bool(cfg["endpoints"])
        self._fill_pick(self.CmbPost, post_labels, "")
        self._fill_pick(self.CmbFoundation, found_labels, "")
        self._select_pick(self.CmbPost, cfg["post"])
        self._select_pick(self.CmbFoundation, cfg["foundation"])

    # ---- family pickers (post + foundation share the behaviour) ------
    @staticmethod
    def _fill_pick(combo, labels, needle):
        combo.Items.Clear()
        combo.Items.Add(NONE_LABEL)
        needle = (needle or "").strip().lower()
        for lbl in labels:
            if needle and needle not in lbl.lower():
                continue
            combo.Items.Add(lbl)
        combo.SelectedIndex = 0

    @staticmethod
    def _select_pick(combo, label):
        if not label:
            combo.SelectedIndex = 0
            return
        for i in range(combo.Items.Count):
            if str(combo.Items[i]) == label:
                combo.SelectedIndex = i
                return
        # saved on another model / filtered away - show it anyway
        combo.Items.Add(label)
        combo.SelectedIndex = combo.Items.Count - 1

    @staticmethod
    def _picked(combo):
        lbl = str(combo.SelectedItem or NONE_LABEL)
        return "" if lbl == NONE_LABEL else lbl

    def on_post_search(self, sender, args):
        try:
            keep = self._picked(self.CmbPost)
            self._fill_pick(self.CmbPost, self.post_labels,
                            self.TxtPostSearch.Text)
            self._select_pick(self.CmbPost, keep)
        except Exception:
            pass

    def on_found_search(self, sender, args):
        try:
            keep = self._picked(self.CmbFoundation)
            self._fill_pick(self.CmbFoundation, self.found_labels,
                            self.TxtFoundSearch.Text)
            self._select_pick(self.CmbFoundation, keep)
        except Exception:
            pass

    # ---- save / cancel -----------------------------------------------
    def on_save(self, sender, args):
        name = (self.TxtName.Text or "").strip()
        if not name:
            self.StatusText.Text = "The configuration needs a name."
            return
        try:
            spacing = float(self.TxtSpacing.Text)
        except Exception:
            spacing = 0.0
        if spacing <= 0:
            self.StatusText.Text = ("Spacing must be a positive "
                                    "number (mm).")
            return
        try:
            rotation = float(self.TxtRotation.Text or 0.0)
        except Exception:
            self.StatusText.Text = ("Rotation must be a number "
                                    "(degrees).")
            return
        post = self._picked(self.CmbPost)
        foundation = self._picked(self.CmbFoundation)
        if not post and not foundation:
            self.StatusText.Text = ("Both post and foundation are "
                                    "'(none)' - this configuration "
                                    "would place nothing.")
            return
        self.result = {"name": name, "spacing": spacing,
                       "endpoints": bool(self.ChkEnds.IsChecked),
                       "rotation": rotation, "post": post,
                       "foundation": foundation}
        self.Close()

    def on_cancel(self, sender, args):
        self.result = None
        self.Close()


class ConfigsWindow(forms.WPFWindow):
    """The list window: rows + Add new / Edit / Remove."""

    def __init__(self, settings, post_labels, found_labels):
        forms.WPFWindow.__init__(self, XAML_LIST)
        self.settings = settings
        self.post_labels = post_labels
        self.found_labels = found_labels
        self._names = []
        self._fill(settings.get(F.SETTINGS_LAST))

    def _fill(self, want):
        cfgs = F.get_configs(self.settings)
        self._names = sorted(cfgs.keys(), key=lambda s: s.lower())
        self.LstConfigs.Items.Clear()
        for n in self._names:
            self.LstConfigs.Items.Add(_row_text(n, cfgs[n]))
        pick = want if want in cfgs else self._names[0]
        self.LstConfigs.SelectedIndex = self._names.index(pick)
        self.TxtInfo.Text = ("{} configuration(s) - used by Place "
                             "New Fence and Update Fence.".format(
                                 len(self._names)))

    def _selected_name(self):
        i = self.LstConfigs.SelectedIndex
        if 0 <= i < len(self._names):
            return self._names[i]
        return None

    def _persist(self):
        try:
            save_settings(self.settings)
        except Exception:
            pass

    def _run_editor(self, title, name, cfg, editing):
        """Open the editor; on Save, store (renaming when the name
        changed on an edit)."""
        win = ConfigEditWindow(title, name, cfg, self.post_labels,
                               self.found_labels)
        win.ShowDialog()
        r = win.result
        if r is None:
            return
        try:
            F.upsert_config(self.settings, r["name"], r["spacing"],
                            r["endpoints"], r["rotation"],
                            r["foundation"], r["post"])
        except ValueError as ex:
            self.StatusText.Text = str(ex)
            return
        if editing and name and r["name"] != name:
            F.delete_config(self.settings, name)
        self._persist()
        self._fill(r["name"])
        self.StatusText.Text = ""

    def on_add(self, sender, args):
        cfgs = F.get_configs(self.settings)
        n = 2
        name = "New config"
        while name in cfgs:
            name = "New config {}".format(n)
            n += 1
        self._run_editor("Add fence configuration", name,
                         dict(F.DEFAULT_CONFIG), editing=False)

    def on_edit(self, sender, args):
        name = self._selected_name()
        cfg = F.get_configs(self.settings).get(name)
        if cfg is None:
            return
        self._run_editor("Edit fence configuration - {}".format(name),
                         name, cfg, editing=True)

    def on_remove(self, sender, args):
        try:
            name = self._selected_name()
            if name is None:
                return
            F.delete_config(self.settings, name)
            self._persist()
            self._fill(None)
            self.StatusText.Text = ""
        except Exception as ex:
            self.StatusText.Text = str(ex)

    def on_close(self, sender, args):
        self.Close()


post_labels = [lbl for lbl, _fs
               in FR.placeable_symbols(doc, F.POST_CATEGORIES)]
found_labels = [lbl for lbl, _fs
                in FR.placeable_symbols(doc, F.FOUNDATION_CATEGORIES)]
ConfigsWindow(load_settings(), post_labels,
              found_labels).ShowDialog()
script.exit()
