# -*- coding: utf-8 -*-
"""Fence Configurations - the list of named set-ups used by Place
New Fence and Update Fence, with Add / Remove / Save.

Each configuration: spacing (mm), place-at-endpoints, a custom
ROTATION in degrees on top of line-aligned (0 follows the line, 90
stands across it), the POST family (Generic Models / Columns /
Structural Columns) and the FOUNDATION family (Structural
Foundations) - either may be '(none)'. Saved in pyMEP settings, so
they survive updates; Update Fence re-reads them by name, so an edit
here re-spaces / re-rotates / re-founds its fences on the next
update.
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

XAML_PATH = os.path.join(
    os.path.dirname(os.path.abspath(sys.modules["pymep_config"].__file__)),
    "pymep_fence_configs.xaml")

NONE_LABEL = "(none)"


def _row_text(name, cfg):
    return (u"{}  —  {:g} mm, ends {}, rot {:+g}°  |  post: {}  |  "
            u"fnd: {}".format(
                name, cfg["spacing_mm"],
                "ON" if cfg["endpoints"] else "off",
                cfg["rotation_deg"],
                cfg["post"] or "none",
                cfg["foundation"] or "none"))


class ConfigsWindow(forms.WPFWindow):
    def __init__(self, settings, post_labels, found_labels):
        forms.WPFWindow.__init__(self, XAML_PATH)
        self.settings = settings
        self.post_labels = post_labels
        self.found_labels = found_labels
        self._names = []
        self._fill_pick(self.CmbPost, post_labels, "")
        self._fill_pick(self.CmbFoundation, found_labels, "")
        self._fill(settings.get(F.SETTINGS_LAST))

    # ---- config list -------------------------------------------------
    def _fill(self, want):
        cfgs = F.get_configs(self.settings)
        self._names = sorted(cfgs.keys(), key=lambda s: s.lower())
        self.LstConfigs.Items.Clear()
        for n in self._names:
            self.LstConfigs.Items.Add(_row_text(n, cfgs[n]))
        pick = want if want in cfgs else self._names[0]
        self.LstConfigs.SelectedIndex = self._names.index(pick)

    def _selected_name(self):
        i = self.LstConfigs.SelectedIndex
        if 0 <= i < len(self._names):
            return self._names[i]
        return None

    def on_pick(self, sender, args):
        try:
            name = self._selected_name()
            cfg = F.get_configs(self.settings).get(name)
            if cfg is None:
                return
            self.TxtName.Text = name
            self.TxtSpacing.Text = "{:g}".format(cfg["spacing_mm"])
            self.TxtRotation.Text = "{:g}".format(cfg["rotation_deg"])
            self.ChkEnds.IsChecked = bool(cfg["endpoints"])
            self._select_pick(self.CmbPost, cfg["post"])
            self._select_pick(self.CmbFoundation, cfg["foundation"])
            self.StatusText.Text = ""
        except Exception:
            pass

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

    # ---- add / remove / save -----------------------------------------
    def _persist(self):
        try:
            save_settings(self.settings)
        except Exception:
            pass

    def on_add(self, sender, args):
        cfgs = F.get_configs(self.settings)
        n = 2
        name = "New config"
        while name in cfgs:
            name = "New config {}".format(n)
            n += 1
        F.upsert_config(self.settings, name,
                        F.DEFAULT_CONFIG["spacing_mm"],
                        F.DEFAULT_CONFIG["endpoints"],
                        F.DEFAULT_CONFIG["rotation_deg"],
                        F.DEFAULT_CONFIG["foundation"],
                        F.DEFAULT_CONFIG["post"])
        self._persist()
        self._fill(name)
        self.StatusText.Text = ""

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

    def on_save(self, sender, args):
        selected = self._selected_name()
        new_name = (self.TxtName.Text or "").strip()
        post = self._picked(self.CmbPost)
        foundation = self._picked(self.CmbFoundation)
        try:
            F.upsert_config(self.settings, new_name,
                            self.TxtSpacing.Text,
                            bool(self.ChkEnds.IsChecked),
                            self.TxtRotation.Text,
                            foundation, post)
        except ValueError as ex:
            self.StatusText.Text = str(ex)
            return
        except Exception as ex:
            self.StatusText.Text = str(ex)
            return
        # a changed Name RENAMES the picked configuration
        if selected and new_name and selected != new_name:
            F.delete_config(self.settings, selected)
        self._persist()
        self._fill(new_name)
        self.StatusText.Text = ("both post and foundation are "
                                "'(none)' - this configuration "
                                "places nothing"
                                if not post and not foundation
                                else "")

    def on_close(self, sender, args):
        self.Close()


post_labels = [lbl for lbl, _fs
               in FR.placeable_symbols(doc, F.POST_CATEGORIES)]
found_labels = [lbl for lbl, _fs
                in FR.placeable_symbols(doc, F.FOUNDATION_CATEGORIES)]
ConfigsWindow(load_settings(), post_labels,
              found_labels).ShowDialog()
script.exit()
