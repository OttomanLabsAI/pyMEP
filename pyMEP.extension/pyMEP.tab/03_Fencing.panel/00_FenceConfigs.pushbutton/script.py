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


def _row_text(name, cfg, style_names=None):
    txt = (u"{}  —  {:g} mm, ends {}, rot {:+g}°  |  post: {}  |  "
           u"fnd: {}".format(
               name, cfg["spacing_mm"],
               "ON" if cfg["endpoints"] else "off",
               cfg["rotation_deg"],
               cfg["post"] or "none",
               cfg["foundation"] or "none"))
    if not cfg.get("same_end_posts", cfg.get("same_ends", True)):
        txt += u"  |  END post: {}".format(
            cfg["end_post"] or "none")
    if not cfg.get("same_end_foundations",
                   cfg.get("same_ends", True)):
        txt += u"  |  END fnd: {}".format(
            cfg["end_foundation"] or "none")
    if cfg.get("panel"):
        txt += u"  |  panel: {}".format(cfg["panel"])
    if cfg.get("terrain_mode") == F.TERRAIN_PICK:
        txt += u"  |  topo: pick at run"
    elif cfg.get("terrain_mode") == F.TERRAIN_NAMED:
        txt += u"  |  topo: {}".format(
            u", ".join(cfg.get("terrains") or []) or "?")
    if cfg.get("line_style"):
        stale = style_names is not None and \
            cfg["line_style"] not in style_names
        txt += u"  |  NET: '{}'{}{}".format(
            cfg["line_style"],
            " END-PRIORITY" if cfg.get("end_priority") else "",
            u"  !! style NOT in the model (renamed?) - Edit and "
            u"re-pick" if stale else "")
    return txt


class ConfigEditWindow(forms.WPFWindow):
    """The editor: one configuration's values. result dict or None
    on cancel - the caller persists."""

    def __init__(self, title, name, cfg, post_labels, found_labels,
                 style_names, panel_labels, terrain_labels):
        forms.WPFWindow.__init__(self, XAML_EDIT)
        self.result = None
        self.post_labels = post_labels
        self.found_labels = found_labels
        self.panel_labels = panel_labels
        # column size options come from the SELECTED post family -
        # mutable lists so the search + red-tint closures follow
        self.colsize_labels = []
        self.end_colsize_labels = []
        self._colsize_cache = {}
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
        self._refresh_colsize(self.CmbPost, self.CmbColSize,
                              self.colsize_labels,
                              cfg["post_col_size"])
        self._refresh_colsize(self.CmbEndPost, self.CmbEndColSize,
                              self.end_colsize_labels,
                              cfg["end_post_col_size"])
        self.CmbPost.SelectionChanged += self._on_post_changed
        self.CmbEndPost.SelectionChanged += self._on_end_post_changed
        self.TxtPostFndDepth.Text = cfg["post_fnd_depth"]
        self.TxtPostHeight.Text = cfg["post_height"]
        self.TxtEndPostFndDepth.Text = cfg["end_post_fnd_depth"]
        self.TxtEndPostHeight.Text = cfg["end_post_height"]
        self.TxtFndEmbed.Text = cfg["fnd_embedment"]
        self.TxtFndDia.Text = cfg["fnd_diameter"]
        self.TxtFndDepth.Text = cfg["fnd_depth"]
        self.TxtEndFndEmbed.Text = cfg["end_fnd_embedment"]
        self.TxtEndFndDia.Text = cfg["end_fnd_diameter"]
        self.TxtEndFndDepth.Text = cfg["end_fnd_depth"]
        self._fill_pick(self.CmbPanel, panel_labels, "")
        self._select_pick(self.CmbPanel, cfg["panel"])
        self.TxtPanelParam.Text = cfg["panel_width_param"]
        self.TxtEastParam.Text = cfg.get("easting_param") or \
            F.EASTING_PARAM
        self.TxtNorthParam.Text = cfg.get("northing_param") or \
            F.NORTHING_PARAM
        self.ChkSameEnds.IsChecked = bool(cfg["same_end_posts"])
        self.ChkSameFnds.IsChecked = bool(
            cfg["same_end_foundations"])
        self.on_same_ends(None, None)
        self.on_same_fnds(None, None)
        self.CmbLineStyle.Items.Clear()
        self.CmbLineStyle.Items.Add(NONE_LABEL)
        for nm2 in style_names:
            self.CmbLineStyle.Items.Add(nm2)
        self._select_pick(self.CmbLineStyle, cfg["line_style"])
        self.ChkEndPriority.IsChecked = bool(cfg["end_priority"])
        # terrain: mode radios + the multi-select name list (stored
        # names missing from this model stay listed so they survive
        # - shown RED)
        stored = list(cfg.get("terrains") or [])
        self.model_terrains = set(terrain_labels)
        self.terrain_labels = list(terrain_labels) + \
            [n for n in stored if n not in terrain_labels]
        self._terr_sel = set(stored)
        self._terr_busy = False
        mode = cfg.get("terrain_mode") or F.TERRAIN_AUTO
        self.RbTerrainPick.IsChecked = (mode == F.TERRAIN_PICK)
        self.RbTerrainNamed.IsChecked = (mode == F.TERRAIN_NAMED)
        self.RbTerrainAuto.IsChecked = mode not in (
            F.TERRAIN_PICK, F.TERRAIN_NAMED)
        self._fill_terrains("")
        self.on_terrain_mode(None, None)
        # RED marks any pick that is NOT in this model any more
        self.style_names = list(style_names)
        for combo, labels in (
                (self.CmbPost, self.post_labels),
                (self.CmbEndPost, self.post_labels),
                (self.CmbFoundation, self.found_labels),
                (self.CmbEndFoundation, self.found_labels),
                (self.CmbPanel, self.panel_labels),
                (self.CmbColSize, self.colsize_labels),
                (self.CmbEndColSize, self.end_colsize_labels),
                (self.CmbLineStyle, self.style_names)):
            combo.SelectionChanged += self._make_tint(combo, labels)
            self._tint_known(combo, labels)

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

    def _make_tint(self, combo, labels):
        def _h(sender, args):
            self._tint_known(combo, labels)
        return _h

    @staticmethod
    def _tint_known(combo, labels):
        """RED when the current pick is NOT in this model any more
        (saved on another model, or renamed / deleted since) - the
        value is kept, the colour says re-pick."""
        try:
            from System.Windows.Media import Brushes
            v = combo.SelectedItem
            bad = v is not None and str(v) != NONE_LABEL and \
                str(v) not in labels
            combo.Foreground = Brushes.Red if bad else Brushes.Black
            combo.ToolTip = ("NOT in this model (renamed or "
                             "deleted?) - re-pick"
                             if bad else None)
        except Exception:
            pass

    def _apply_search(self, combo, labels, box, key):
        """Typing NARROWS the list and jumps the pick to the first
        match (so the combo visibly changes); clearing the box
        restores the full list and the last real pick. The dropdown
        is NOT opened - that would steal the keyboard from the
        search box mid-word."""
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
            if not box.IsKeyboardFocusWithin:
                box.Focus()
                box.CaretIndex = len(box.Text or "")
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

    def _refresh_colsize(self, post_combo, cs_combo, labels_list,
                         keep=None):
        """Re-pull the COLUMN SIZE options from the family picked in
        ``post_combo`` - the dropdown shows exactly what the family
        offers."""
        try:
            if keep is None:
                keep = self._picked(cs_combo)
            lbl = self._picked(post_combo)
            if lbl in self._colsize_cache:
                opts = self._colsize_cache[lbl]
            else:
                sym = FR.symbol_by_label(doc, lbl,
                                         F.POST_CATEGORIES) \
                    if lbl else None
                opts = FR.family_type_options(doc, sym,
                                              F.COL_SIZE_PARAM)
                self._colsize_cache[lbl] = opts
            labels_list[:] = opts
            self._fill_pick(cs_combo, labels_list, "")
            self._select_pick(cs_combo, keep)
        except Exception:
            pass

    def _on_post_changed(self, sender, args):
        self._refresh_colsize(self.CmbPost, self.CmbColSize,
                              self.colsize_labels)

    def _on_end_post_changed(self, sender, args):
        self._refresh_colsize(self.CmbEndPost, self.CmbEndColSize,
                              self.end_colsize_labels)

    def on_colsize_search(self, sender, args):
        try:
            self._apply_search(self.CmbColSize, self.colsize_labels,
                               self.TxtColSizeSearch, "colsize")
        except Exception:
            pass

    def on_end_colsize_search(self, sender, args):
        try:
            self._apply_search(self.CmbEndColSize,
                               self.end_colsize_labels,
                               self.TxtEndColSizeSearch,
                               "end_colsize")
        except Exception:
            pass

    @staticmethod
    def _show(panel, on):
        """HIDE what is not needed (collapsed, not greyed)."""
        try:
            from System.Windows import Visibility
            panel.Visibility = Visibility.Visible if on \
                else Visibility.Collapsed
        except Exception:
            panel.IsEnabled = on

    def on_same_ends(self, sender, args):
        try:
            self._show(self.PnlEndPost,
                       not bool(self.ChkSameEnds.IsChecked))
        except Exception:
            pass

    def on_same_fnds(self, sender, args):
        try:
            self._show(self.PnlEndFound,
                       not bool(self.ChkSameFnds.IsChecked))
        except Exception:
            pass

    # ---- terrain (mode radios + multi-select name list) --------------
    def _fill_terrains(self, needle):
        """Rebuild the list to the filter, re-ticking the names in
        the selection set - names NOT in this model show RED."""
        self._terr_busy = True
        try:
            from System.Windows.Controls import ListBoxItem
            from System.Windows.Media import Brushes
            self.LstTerrains.Items.Clear()
            needle = (needle or "").strip().lower()
            for lbl in self.terrain_labels:
                if needle and needle not in lbl.lower():
                    continue
                it = ListBoxItem()
                it.Content = lbl
                if lbl not in self.model_terrains:
                    it.Foreground = Brushes.Red
                    it.ToolTip = ("NOT in this model (renamed or "
                                  "deleted?)")
                self.LstTerrains.Items.Add(it)
            for it in list(self.LstTerrains.Items):
                if str(it.Content) in self._terr_sel:
                    self.LstTerrains.SelectedItems.Add(it)
        finally:
            self._terr_busy = False

    def on_terrain_search(self, sender, args):
        try:
            self._fill_terrains(self.TxtTerrainSearch.Text)
        except Exception:
            pass

    def on_terrain_pick(self, sender, args):
        """Keep the selection SET in step: ticks made through any
        filter accumulate, unticking only drops what is visible."""
        if getattr(self, "_terr_busy", True):
            return
        try:
            visible = set(str(it.Content)
                          for it in self.LstTerrains.Items)
            picked = set(str(it.Content)
                         for it in self.LstTerrains.SelectedItems)
            self._terr_sel = (self._terr_sel - visible) | picked
            if picked and not bool(
                    self.RbTerrainNamed.IsChecked):
                self.RbTerrainNamed.IsChecked = True
        except Exception:
            pass

    def on_terrain_mode(self, sender, args):
        try:
            self._show(self.PnlTerrains,
                       bool(self.RbTerrainNamed.IsChecked))
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
        same_posts = bool(self.ChkSameEnds.IsChecked)
        same_fnds = bool(self.ChkSameFnds.IsChecked)
        same_ends = same_posts and same_fnds
        end_post = self._picked(self.CmbEndPost)
        end_foundation = self._picked(self.CmbEndFoundation)
        probe = {"post": post, "foundation": foundation,
                 "endpoints": bool(self.ChkEnds.IsChecked),
                 "same_end_posts": same_posts,
                 "same_end_foundations": same_fnds,
                 "end_post": end_post,
                 "end_foundation": end_foundation}
        if not F.places_something(probe):
            self.StatusText.Text = ("This configuration would place "
                                    "NOTHING - every family is "
                                    "'(none)'.")
            return
        if bool(self.RbTerrainAuto.IsChecked):
            terrain_mode = F.TERRAIN_AUTO
        elif bool(self.RbTerrainNamed.IsChecked):
            terrain_mode = F.TERRAIN_NAMED
        else:
            terrain_mode = F.TERRAIN_PICK
        terrains = [n for n in self.terrain_labels
                    if n in self._terr_sel]
        if terrain_mode == F.TERRAIN_NAMED and not terrains:
            self.StatusText.Text = ("Tick at least one terrain "
                                    "element - or choose another "
                                    "terrain option.")
            return

        self.result = {"name": name, "spacing": spacing,
                       "endpoints": bool(self.ChkEnds.IsChecked),
                       "rotation": rotation, "post": post,
                       "foundation": foundation,
                       "same_ends": same_ends,
                       "same_end_posts": same_posts,
                       "same_end_foundations": same_fnds,
                       "end_post": end_post,
                       "end_foundation": end_foundation,
                       "line_style":
                           self._picked(self.CmbLineStyle),
                       "end_priority":
                           bool(self.ChkEndPriority.IsChecked),
                       "panel": self._picked(self.CmbPanel),
                       "panel_width_param":
                           (self.TxtPanelParam.Text or "").strip(),
                       "easting_param":
                           (self.TxtEastParam.Text or "").strip(),
                       "northing_param":
                           (self.TxtNorthParam.Text or "").strip(),
                       "terrain_mode": terrain_mode,
                       "terrains": terrains,
                       "dims": {
                           "post_col_size":
                               self._picked(self.CmbColSize),
                           "post_fnd_depth":
                               (self.TxtPostFndDepth.Text
                                or "").strip(),
                           "post_height":
                               (self.TxtPostHeight.Text
                                or "").strip(),
                           "end_post_col_size":
                               self._picked(self.CmbEndColSize),
                           "end_post_fnd_depth":
                               (self.TxtEndPostFndDepth.Text
                                or "").strip(),
                           "end_post_height":
                               (self.TxtEndPostHeight.Text
                                or "").strip(),
                           "fnd_embedment":
                               (self.TxtFndEmbed.Text
                                or "").strip(),
                           "fnd_diameter":
                               (self.TxtFndDia.Text or "").strip(),
                           "fnd_depth":
                               (self.TxtFndDepth.Text
                                or "").strip(),
                           "end_fnd_embedment":
                               (self.TxtEndFndEmbed.Text
                                or "").strip(),
                           "end_fnd_diameter":
                               (self.TxtEndFndDia.Text
                                or "").strip(),
                           "end_fnd_depth":
                               (self.TxtEndFndDepth.Text
                                or "").strip()}}
        self.Close()

    def on_cancel(self, sender, args):
        self.result = None
        self.Close()


class ConfigsWindow(forms.WPFWindow):
    """The list window: rows + Add new / Edit / Remove."""

    def __init__(self, settings, post_labels, found_labels,
                 style_names, panel_labels, terrain_labels):
        forms.WPFWindow.__init__(self, XAML_LIST)
        self.settings = settings
        self.post_labels = post_labels
        self.found_labels = found_labels
        self.style_names = style_names
        self.panel_labels = panel_labels
        self.terrain_labels = terrain_labels
        self._names = []
        self._mark_busy = False
        self._fill(settings.get(F.SETTINGS_LAST))

    def _fill(self, want):
        cfgs = F.get_configs(self.settings)
        self._names = F.priority_order(cfgs)
        self.LstConfigs.Items.Clear()
        for i, n in enumerate(self._names):
            self.LstConfigs.Items.Add(u"{}.  {}".format(
                i + 1, _row_text(n, cfgs[n], self.style_names)))
        pick = want if want in cfgs else self._names[0]
        self.LstConfigs.SelectedIndex = self._names.index(pick)
        on, pref = F.mark_settings(self.settings)
        self.TxtInfo.Text = ("{} configuration(s), top wins corner "
                             "posts in Fence Network.{}".format(
                                 len(self._names),
                                 "  MARK numbering ON{}.".format(
                                     " - prefix '{}'".format(pref)
                                     if pref else "")
                                 if on else ""))
        self._sync_mark()

    def _sync_mark(self):
        """The GLOBAL ticks: MARK numbering + prefix, TOC param +
        equation."""
        self._mark_busy = True
        try:
            on, pref = F.mark_settings(self.settings)
            self.ChkMark.IsChecked = on
            if self.TxtMarkPrefix.Text != pref:
                self.TxtMarkPrefix.Text = pref
            t_on, t_par, t_eq = F.toc_settings(self.settings)
            self.ChkToc.IsChecked = t_on
            if self.TxtTocParam.Text != t_par:
                self.TxtTocParam.Text = t_par
            if self.TxtTocFormula.Text != t_eq:
                self.TxtTocFormula.Text = t_eq
            self.TxtTocParam.IsEnabled = t_on
            self.TxtTocFormula.IsEnabled = t_on
        finally:
            self._mark_busy = False

    def on_row_selected(self, sender, args):
        pass

    def on_mark_toggle(self, sender, args):
        """GLOBAL: every configuration numbers its posts (applies
        on the next model / Update Fence run)."""
        if getattr(self, "_mark_busy", True):
            return
        try:
            self.settings[F.SETTINGS_MARK] = bool(
                self.ChkMark.IsChecked)
            self._persist()
            self._fill(self._selected_name())
            self.StatusText.Text = ""
        except Exception as ex:
            self.StatusText.Text = str(ex)

    def on_mark_prefix(self, sender, args):
        if getattr(self, "_mark_busy", True):
            return
        try:
            self.settings[F.SETTINGS_MARK_PREFIX] = \
                (self.TxtMarkPrefix.Text or "").strip()
            self._persist()
        except Exception:
            pass

    def on_toc_toggle(self, sender, args):
        """GLOBAL: every foundation gets the TOC equation's value
        (applies on the next place / move / Update Fence run)."""
        if getattr(self, "_mark_busy", True):
            return
        try:
            self.settings[F.SETTINGS_TOC] = bool(
                self.ChkToc.IsChecked)
            self._persist()
            self._sync_mark()
            self.StatusText.Text = ""
        except Exception as ex:
            self.StatusText.Text = str(ex)

    def on_toc_param(self, sender, args):
        if getattr(self, "_mark_busy", True):
            return
        try:
            self.settings[F.SETTINGS_TOC_PARAM] = \
                (self.TxtTocParam.Text or "").strip()
            self._persist()
        except Exception:
            pass

    def on_toc_formula(self, sender, args):
        """Save the equation as typed - parameter names resolve per
        foundation at run time, so no early check here; the run
        reports the first problem it hits."""
        if getattr(self, "_mark_busy", True):
            return
        try:
            self.settings[F.SETTINGS_TOC_FORMULA] = \
                (self.TxtTocFormula.Text or "").strip()
            self._persist()
        except Exception:
            pass

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
                               self.panel_labels,
                               self.terrain_labels)
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
                            r["panel_width_param"],
                            r["easting_param"], r["northing_param"],
                            r["terrain_mode"], r["terrains"],
                            r["same_end_posts"],
                            r["same_end_foundations"], r["dims"])
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
terrain_labels = sorted(set(
    FR.element_name(el) for el in FR.terrain_elements(doc)))
ConfigsWindow(load_settings(), post_labels, found_labels,
              style_names, panel_labels,
              terrain_labels).ShowDialog()
script.exit()
