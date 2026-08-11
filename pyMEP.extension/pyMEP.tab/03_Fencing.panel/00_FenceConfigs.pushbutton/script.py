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
    txt = (u"{}  —  {:g} mm, ends {}, rot {:+g}°  |  post: {}  |  "
           u"fnd: {}".format(
               name, cfg["spacing_mm"],
               "ON" if cfg["endpoints"] else "off",
               cfg["rotation_deg"],
               cfg["post"] or "none",
               cfg["foundation"] or "none"))
    if not cfg.get("same_ends", True):
        txt += u"  |  END post: {} / fnd: {}".format(
            cfg["end_post"] or "none",
            cfg["end_foundation"] or "none")
    if cfg.get("panel"):
        txt += u"  |  panel: {}".format(cfg["panel"])
    if cfg.get("line_style"):
        txt += u"  |  NET: '{}'{}".format(
            cfg["line_style"],
            " END-PRIORITY" if cfg.get("end_priority") else "")
    return txt


class ConfigEditWindow(forms.WPFWindow):
    """The editor: one configuration's values. result dict or None
    on cancel - the caller persists."""

    def __init__(self, title, name, cfg, post_labels, found_labels,
                 style_names, panel_labels):
        forms.WPFWindow.__init__(self, XAML_EDIT)
        self.result = None
        self.post_labels = post_labels
        self.found_labels = found_labels
        self.panel_labels = panel_labels
        self._last = {}     # last real pick per combo, survives filters
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
        self._fill_pick(self.CmbEndPost, post_labels, "")
        self._fill_pick(self.CmbEndFoundation, found_labels, "")
        self._select_pick(self.CmbEndPost, cfg["end_post"])
        self._select_pick(self.CmbEndFoundation,
                          cfg["end_foundation"])
        self._fill_pick(self.CmbPanel, panel_labels, "")
        self._select_pick(self.CmbPanel, cfg["panel"])
        self.TxtPanelParam.Text = cfg["panel_width_param"]
        self.ChkSameEnds.IsChecked = bool(cfg["same_ends"])
        self.on_same_ends(None, None)
        self.CmbLineStyle.Items.Clear()
        self.CmbLineStyle.Items.Add(NONE_LABEL)
        for nm2 in style_names:
            self.CmbLineStyle.Items.Add(nm2)
        self._select_pick(self.CmbLineStyle, cfg["line_style"])
        self.ChkEndPriority.IsChecked = bool(cfg["end_priority"])

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

    def _apply_search(self, combo, labels, box, key):
        """Typing NARROWS the list, jumps the pick to the first match
        (so the combo visibly changes) and drops the list open;
        clearing the box restores the full list and the last real
        pick."""
        needle = (box.Text or "").strip()
        current = self._picked(combo)
        if current:
            self._last[key] = current
        self._fill_pick(combo, labels, needle)
        want = None
        # the previous pick wins whenever it still matches the filter
        for cand in (current, self._last.get(key)):
            if want is not None:
                break
            if cand:
                for i in range(combo.Items.Count):
                    if str(combo.Items[i]) == cand:
                        want = i
                        break
        if want is None:
            # first real match - '(none)' sits at index 0
            want = 1 if (needle and combo.Items.Count > 1) else 0
        combo.SelectedIndex = want
        try:
            combo.IsDropDownOpen = bool(needle)
        except Exception:
            pass

    def on_post_search(self, sender, args):
        try:
            self._apply_search(self.CmbPost, self.post_labels,
                               self.TxtPostSearch, "post")
        except Exception:
            pass

    def on_found_search(self, sender, args):
        try:
            self._apply_search(self.CmbFoundation, self.found_labels,
                               self.TxtFoundSearch, "fnd")
        except Exception:
            pass

    def on_end_post_search(self, sender, args):
        try:
            self._apply_search(self.CmbEndPost, self.post_labels,
                               self.TxtEndPostSearch, "end_post")
        except Exception:
            pass

    def on_panel_search(self, sender, args):
        try:
            self._apply_search(self.CmbPanel, self.panel_labels,
                               self.TxtPanelSearch, "panel")
        except Exception:
            pass

    def on_end_found_search(self, sender, args):
        try:
            self._apply_search(self.CmbEndFoundation,
                               self.found_labels,
                               self.TxtEndFoundSearch, "end_fnd")
        except Exception:
            pass

    def on_same_ends(self, sender, args):
        try:
            on = not bool(self.ChkSameEnds.IsChecked)
            self.PnlEndPost.IsEnabled = on
            self.PnlEndFound.IsEnabled = on
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
        same_ends = bool(self.ChkSameEnds.IsChecked)
        end_post = self._picked(self.CmbEndPost)
        end_foundation = self._picked(self.CmbEndFoundation)
        probe = {"post": post, "foundation": foundation,
                 "endpoints": bool(self.ChkEnds.IsChecked),
                 "same_ends": same_ends, "end_post": end_post,
                 "end_foundation": end_foundation}
        if not F.places_something(probe):
            self.StatusText.Text = ("This configuration would place "
                                    "NOTHING - every family is "
                                    "'(none)'.")
            return
        self.result = {"name": name, "spacing": spacing,
                       "endpoints": bool(self.ChkEnds.IsChecked),
                       "rotation": rotation, "post": post,
                       "foundation": foundation,
                       "same_ends": same_ends, "end_post": end_post,
                       "end_foundation": end_foundation,
                       "line_style":
                           self._picked(self.CmbLineStyle),
                       "end_priority":
                           bool(self.ChkEndPriority.IsChecked),
                       "panel": self._picked(self.CmbPanel),
                       "panel_width_param":
                           (self.TxtPanelParam.Text or "").strip()}
        self.Close()

    def on_cancel(self, sender, args):
        self.result = None
        self.Close()


class ConfigsWindow(forms.WPFWindow):
    """The list window: rows + Add new / Edit / Remove."""

    def __init__(self, settings, post_labels, found_labels,
                 style_names, panel_labels):
        forms.WPFWindow.__init__(self, XAML_LIST)
        self.settings = settings
        self.post_labels = post_labels
        self.found_labels = found_labels
        self.style_names = style_names
        self.panel_labels = panel_labels
        self._names = []
        self._fill(settings.get(F.SETTINGS_LAST))

    def _fill(self, want):
        cfgs = F.get_configs(self.settings)
        self._names = F.priority_order(cfgs)
        self.LstConfigs.Items.Clear()
        for i, n in enumerate(self._names):
            self.LstConfigs.Items.Add(u"{}.  {}".format(
                i + 1, _row_text(n, cfgs[n])))
        pick = want if want in cfgs else self._names[0]
        self.LstConfigs.SelectedIndex = self._names.index(pick)
        self.TxtInfo.Text = ("{} configuration(s), top wins corner "
                             "posts in Fence Network.".format(
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
        changed on an edit). The priority comes from the LIST: an
        edit keeps its place, a new config joins at the bottom."""
        win = ConfigEditWindow(title, name, cfg, self.post_labels,
                               self.found_labels, self.style_names,
                               self.panel_labels)
        win.ShowDialog()
        r = win.result
        if r is None:
            return
        if editing:
            prio = int(cfg.get("priority") or 99)
        else:
            cfgs_now = F.get_configs(self.settings)
            prio = 1 + max([0] + [int(c.get("priority") or 0)
                                  for c in cfgs_now.values()])
        try:
            F.upsert_config(self.settings, r["name"], r["spacing"],
                            r["endpoints"], r["rotation"],
                            r["foundation"], r["post"],
                            r["same_ends"], r["end_post"],
                            r["end_foundation"], r["line_style"],
                            prio, r["end_priority"], r["panel"],
                            r["panel_width_param"])
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

    def _move(self, delta):
        i = self.LstConfigs.SelectedIndex
        j = i + delta
        if i < 0 or j < 0 or j >= len(self._names):
            return
        order = list(self._names)
        order[i], order[j] = order[j], order[i]
        F.renumber_priorities(self.settings, order)
        self._persist()
        self._fill(order[j])
        self.StatusText.Text = ""

    def on_up(self, sender, args):
        try:
            self._move(-1)
        except Exception as ex:
            self.StatusText.Text = str(ex)

    def on_down(self, sender, args):
        try:
            self._move(1)
        except Exception as ex:
            self.StatusText.Text = str(ex)

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
from pymep_vt_serialize import line_style_subcategories
style_names = sorted(set(
    s2.Name for s2 in line_style_subcategories(doc)))
panel_labels = [lbl for lbl, _fs
                in FR.placeable_symbols(doc, F.PANEL_CATEGORIES)]
ConfigsWindow(load_settings(), post_labels, found_labels,
              style_names, panel_labels).ShowDialog()
script.exit()
