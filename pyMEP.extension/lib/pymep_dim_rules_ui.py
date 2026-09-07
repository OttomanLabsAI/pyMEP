# -*- coding: utf-8 -*-
"""The reference-plane dimension RULE LIST shared by the Dimension Section
dialog and the pipeline's Dimensions tab: a list box of rules, a '+' that
opens an editor, Edit / Remove, and the editor's Add-to-list / Cancel.

A rule is either a reference-plane string (axis, numbers, chain / direct,
skip, inside) or a pipe / conduit / duct centreline string (categories,
column spacing above the bank, row spacing left of it); the editor shows
one set of fields or the other.

The window must carry these named controls (same names in both XAMLs):
  LstRules, BtnRuleAdd, BtnRuleEdit, BtnRuleRemove, PnlRuleEditor,
  RbKindPlanes, RbKindPipes, PnlPlanesFields, PnlPipesFields,
  RbAxisZ, RbAxisX, RbAxisY, TxtSpec, RbRuleChain, RbRuleDirect,
  ChkRuleSkip, ChkRuleInside, ChkCatPipe, ChkCatConduit, ChkCatDuct,
  ChkPipeCols, ChkPipeRows, TxtRuleStatus, BtnRuleSave, BtnRuleCancel
and, for the named SETS of dimensions (a rule list saved under a name,
picked from a dropdown):
  CmbDimSet, BtnSetSave, BtnSetDelete, PnlSetSave, TxtSetName,
  BtnSetSaveOk, BtnSetSaveCancel, TxtSetStatus
and forward its on_rule_* / on_kind_changed / on_set_* handlers to the
matching methods here. Sets are written to the settings file the moment
they are saved or deleted, so they survive a cancelled dialog.

Runs under IronPython inside Revit only (WPF); nothing here is imported by
the CPython tests - the rule maths lives in pymep_chamber_sections."""

import pymep_chamber_sections as CS


class RuleList(object):

    def __init__(self, win, rules, settings=None, save=None):
        self.win = win
        self.settings = settings if settings is not None else {}
        self._save = save             # callable(settings) writing the file
        self.rules = []
        for r in rules or []:
            n = CS.normalise_rule(r)
            if n is not None:
                self.rules.append(n)
        self._editing = None          # index being edited, None = adding
        self._loading = False         # True while a set is being loaded
        self.refresh()
        self.hide_editor()
        self.hide_set_save()
        self.fill_sets(CS.matching_dim_set(CS.dim_sets(self.settings),
                                           self.rules))

    # -- list ------------------------------------------------------------
    def refresh(self, select=-1):
        w = self.win
        try:
            w.LstRules.Items.Clear()
            for r in self.rules:
                w.LstRules.Items.Add(CS.rule_label(r))
            if 0 <= select < len(self.rules):
                w.LstRules.SelectedIndex = select
        except Exception:
            pass

    def _selected(self):
        try:
            i = self.win.LstRules.SelectedIndex
        except Exception:
            return None
        if i is None or i < 0 or i >= len(self.rules):
            return None
        return i

    # -- editor ------------------------------------------------------------
    def show_editor(self, rule=None):
        from System.Windows import Visibility
        w = self.win
        r = CS.normalise_rule(rule) if rule else None
        if r is None:
            r = {"kind": CS.RULE_PLANES, "axis": "z", "spec": u"1-",
                 "mode": CS.Z_CHAIN, "skip": True, "inside": True}
        pipes = r["kind"] == CS.RULE_PIPES
        planes = r if not pipes else {"axis": "z", "spec": u"1-",
                                      "mode": CS.Z_CHAIN, "skip": True,
                                      "inside": True}
        cent = r if pipes else CS.pipes_rule()
        try:
            (w.RbKindPipes if pipes else w.RbKindPlanes).IsChecked = True
            (w.RbAxisZ if planes["axis"] == "z" else
             w.RbAxisX if planes["axis"] == "x" else w.RbAxisY).IsChecked = True
            w.TxtSpec.Text = planes["spec"]
            (w.RbRuleChain if planes["mode"] == CS.Z_CHAIN
             else w.RbRuleDirect).IsChecked = True
            w.ChkRuleSkip.IsChecked = bool(planes["skip"])
            w.ChkRuleInside.IsChecked = bool(planes["inside"])
            w.ChkCatPipe.IsChecked = "pipe" in cent["cats"]
            w.ChkCatConduit.IsChecked = "conduit" in cent["cats"]
            w.ChkCatDuct.IsChecked = "duct" in cent["cats"]
            w.ChkPipeCols.IsChecked = bool(cent["cols"])
            w.ChkPipeRows.IsChecked = bool(cent["rows"])
            w.TxtRuleStatus.Text = u""
            self.sync_kind()
            w.BtnRuleSave.Content = (u"Save" if self._editing is not None
                                     else u"Add to list")
            w.PnlRuleEditor.Visibility = Visibility.Visible
        except Exception:
            pass

    def hide_editor(self):
        from System.Windows import Visibility
        try:
            self.win.PnlRuleEditor.Visibility = Visibility.Collapsed
        except Exception:
            pass
        self._editing = None

    def _kind(self):
        try:
            if self.win.RbKindPipes.IsChecked:
                return CS.RULE_PIPES
        except Exception:
            pass
        return CS.RULE_PLANES

    def sync_kind(self):
        """Show the plane fields or the centreline fields."""
        from System.Windows import Visibility
        pipes = self._kind() == CS.RULE_PIPES
        try:
            self.win.PnlPlanesFields.Visibility = (
                Visibility.Collapsed if pipes else Visibility.Visible)
            self.win.PnlPipesFields.Visibility = (
                Visibility.Visible if pipes else Visibility.Collapsed)
        except Exception:
            pass

    def on_kind_changed(self):
        self.sync_kind()

    def _read_editor(self):
        w = self.win
        if self._kind() == CS.RULE_PIPES:
            cats = []
            try:
                if w.ChkCatPipe.IsChecked:
                    cats.append("pipe")
                if w.ChkCatConduit.IsChecked:
                    cats.append("conduit")
                if w.ChkCatDuct.IsChecked:
                    cats.append("duct")
                cols = bool(w.ChkPipeCols.IsChecked)
                rows = bool(w.ChkPipeRows.IsChecked)
            except Exception:
                cols = rows = False
            if not cats:
                return None, u"Tick at least one of pipes, conduits, ducts."
            if not (cols or rows):
                return None, u"Tick the column string, the row string or both."
            return CS.pipes_rule(cats, cols, rows), u""
        axis = "z"
        try:
            if w.RbAxisX.IsChecked:
                axis = "x"
            elif w.RbAxisY.IsChecked:
                axis = "y"
        except Exception:
            pass
        spec = u""
        try:
            spec = (w.TxtSpec.Text or u"").strip()
        except Exception:
            pass
        if CS.parse_plane_spec(spec) is None:
            return None, (u"Numbers: use 1-5, 1- (to the highest), all, or "
                          u"a list like 2,3,5.")
        rule = {"axis": axis, "spec": spec or u"all",
                "mode": (CS.Z_DIRECT if w.RbRuleDirect.IsChecked
                         else CS.Z_CHAIN),
                "skip": bool(w.ChkRuleSkip.IsChecked),
                "inside": bool(w.ChkRuleInside.IsChecked)}
        return CS.normalise_rule(rule), u""

    # -- handlers the window forwards to --------------------------------------
    def on_add(self):
        self._editing = None
        self.show_editor()

    def on_edit(self):
        i = self._selected()
        if i is None:
            try:
                self.win.TxtRuleStatus.Text = u"Pick a rule in the list first."
            except Exception:
                pass
            return
        self._editing = i
        self.show_editor(self.rules[i])

    def on_remove(self):
        i = self._selected()
        if i is None:
            return
        del self.rules[i]
        self.hide_editor()
        self.refresh(min(i, len(self.rules) - 1))
        self.mark_custom()

    def on_save(self):
        rule, err = self._read_editor()
        if rule is None:
            try:
                self.win.TxtRuleStatus.Text = err
            except Exception:
                pass
            return
        if self._editing is not None and self._editing < len(self.rules):
            self.rules[self._editing] = rule
            sel = self._editing
        else:
            self.rules.append(rule)
            sel = len(self.rules) - 1
        self.hide_editor()
        self.refresh(sel)
        self.mark_custom()

    def on_cancel(self):
        self.hide_editor()

    # -- named sets -------------------------------------------------------------
    def sets(self):
        return CS.dim_sets(self.settings)

    def selected_set(self):
        """The name picked in the dropdown, None for '(custom)'."""
        try:
            i = self.win.CmbDimSet.SelectedIndex
            item = self.win.CmbDimSet.SelectedItem
        except Exception:
            return None
        if i is None or i <= 0 or item is None:
            return None
        name = u"{0}".format(item)
        return name if name in self.sets() else None

    def fill_sets(self, select=None):
        """Rebuild the dropdown: '(custom)' first, then the saved names;
        select a name, or '(custom)' when None / unknown."""
        w = self.win
        self._loading = True
        try:
            w.CmbDimSet.Items.Clear()
            w.CmbDimSet.Items.Add(CS.CUSTOM_SET_LABEL)
            names = CS.dim_set_names(self.sets())
            for n in names:
                w.CmbDimSet.Items.Add(n)
            w.CmbDimSet.SelectedIndex = (names.index(select) + 1
                                         if select in names else 0)
            w.BtnSetDelete.IsEnabled = select in names
        except Exception:
            pass
        self._loading = False

    def mark_custom(self):
        """The list no longer equals the chosen set: show '(custom)'."""
        if self._loading:
            return
        w = self.win
        self._loading = True
        try:
            if w.CmbDimSet.SelectedIndex != 0:
                w.CmbDimSet.SelectedIndex = 0
            w.BtnSetDelete.IsEnabled = False
        except Exception:
            pass
        self._loading = False

    def load_set(self, name):
        """Replace the rule list with the saved set."""
        one = self.sets().get(name)
        if one is None:
            return
        self._loading = True
        self.rules = [dict(r) for r in one["rules"]]
        self.hide_editor()
        self.hide_set_save()
        self.refresh()
        try:
            self.win.BtnSetDelete.IsEnabled = True
            self.win.TxtSetStatus.Text = u""
        except Exception:
            pass
        self._loading = False

    def show_set_save(self):
        from System.Windows import Visibility
        w = self.win
        try:
            w.TxtSetName.Text = self.selected_set() or u""
            w.TxtSetStatus.Text = u""
            w.PnlSetSave.Visibility = Visibility.Visible
            w.TxtSetName.Focus()
            w.TxtSetName.SelectAll()
        except Exception:
            pass

    def hide_set_save(self):
        from System.Windows import Visibility
        try:
            self.win.PnlSetSave.Visibility = Visibility.Collapsed
        except Exception:
            pass

    def _status(self, text):
        try:
            self.win.TxtSetStatus.Text = text
        except Exception:
            pass

    def _write(self):
        if self._save is None:
            return True
        try:
            self._save(self.settings)
            return True
        except Exception as ex:
            self._status(u"Could not write the settings file: {0}".format(ex))
            return False

    def on_set_changed(self):
        if self._loading:
            return
        name = self.selected_set()
        if name is None:
            try:
                self.win.BtnSetDelete.IsEnabled = False
            except Exception:
                pass
            return
        self.load_set(name)

    def on_set_save(self):
        self.show_set_save()

    def on_set_save_ok(self):
        try:
            typed = self.win.TxtSetName.Text
        except Exception:
            typed = u""
        name, replaced = CS.store_dim_set(self.settings, typed, self.rules)
        if name is None:
            self._status(u"Give the set a name.")
            return
        if not self._write():
            return
        self.hide_set_save()
        self.fill_sets(name)
        self._status(u"Set '{0}' {1}.".format(
            name, u"replaced" if replaced else u"saved"))

    def on_set_save_cancel(self):
        self.hide_set_save()

    def on_set_delete(self):
        name = self.selected_set()
        if name is None:
            self._status(u"Pick a saved set to delete.")
            return
        if not CS.drop_dim_set(self.settings, name):
            return
        if not self._write():
            return
        self.fill_sets(None)
        self._status(u"Set '{0}' deleted - the list below is kept.".format(
            name))
