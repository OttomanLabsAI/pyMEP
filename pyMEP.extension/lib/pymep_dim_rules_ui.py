# -*- coding: utf-8 -*-
"""The reference-plane dimension RULE LIST shared by the Dimension Section
dialog and the pipeline's Dimensions tab: a list box of rules, a '+' that
opens an editor, Edit / Remove, and the editor's Add-to-list / Cancel.

The window must carry these named controls (same names in both XAMLs):
  LstRules, BtnRuleAdd, BtnRuleEdit, BtnRuleRemove, PnlRuleEditor,
  RbAxisZ, RbAxisX, RbAxisY, TxtSpec, RbRuleChain, RbRuleDirect,
  ChkRuleSkip, ChkRuleInside, TxtRuleStatus, BtnRuleSave, BtnRuleCancel
and, for the named SETS of dimensions (pipe tick + rule list saved under a
name, picked from a dropdown):
  ChkPipes, CmbDimSet, BtnSetSave, BtnSetDelete, PnlSetSave, TxtSetName,
  BtnSetSaveOk, BtnSetSaveCancel, TxtSetStatus
and forward its on_rule_* / on_set_* / on_pipes_changed handlers to the
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
                                           self.pipes(), self.rules))

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
            r = {"axis": "z", "spec": u"1-", "mode": CS.Z_CHAIN,
                 "skip": True, "inside": True}
        try:
            (w.RbAxisZ if r["axis"] == "z" else
             w.RbAxisX if r["axis"] == "x" else w.RbAxisY).IsChecked = True
            w.TxtSpec.Text = r["spec"]
            (w.RbRuleChain if r["mode"] == CS.Z_CHAIN
             else w.RbRuleDirect).IsChecked = True
            w.ChkRuleSkip.IsChecked = bool(r["skip"])
            w.ChkRuleInside.IsChecked = bool(r["inside"])
            w.TxtRuleStatus.Text = u""
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

    def _read_editor(self):
        w = self.win
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
    def pipes(self):
        try:
            return bool(self.win.ChkPipes.IsChecked)
        except Exception:
            return True

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
        """Replace the pipe tick and the rule list with the saved set."""
        one = self.sets().get(name)
        if one is None:
            return
        self._loading = True
        try:
            self.win.ChkPipes.IsChecked = bool(one["pipes"])
        except Exception:
            pass
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
        name, replaced = CS.store_dim_set(self.settings, typed, self.pipes(),
                                          self.rules)
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

    def on_pipes_changed(self):
        self.mark_custom()
