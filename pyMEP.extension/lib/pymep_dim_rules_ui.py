# -*- coding: utf-8 -*-
"""The reference-plane dimension RULE LIST shared by the Dimension Section
dialog and the pipeline's Dimensions tab: a list box of rules, a '+' that
opens an editor, Edit / Remove, and the editor's Add-to-list / Cancel.

The window must carry these named controls (same names in both XAMLs):
  LstRules, BtnRuleAdd, BtnRuleEdit, BtnRuleRemove, PnlRuleEditor,
  RbAxisZ, RbAxisX, RbAxisY, TxtSpec, RbRuleChain, RbRuleDirect,
  ChkRuleSkip, ChkRuleInside, TxtRuleStatus, BtnRuleSave, BtnRuleCancel
and forward its on_rule_* handlers to the matching methods here.

Runs under IronPython inside Revit only (WPF); nothing here is imported by
the CPython tests - the rule maths lives in pymep_chamber_sections."""

import pymep_chamber_sections as CS


class RuleList(object):

    def __init__(self, win, rules):
        self.win = win
        self.rules = []
        for r in rules or []:
            n = CS.normalise_rule(r)
            if n is not None:
                self.rules.append(n)
        self._editing = None          # index being edited, None = adding
        self.refresh()
        self.hide_editor()

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

    def on_cancel(self):
        self.hide_editor()
