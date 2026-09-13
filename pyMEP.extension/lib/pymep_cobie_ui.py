# -*- coding: utf-8 -*-
"""The COBie and Uniclass dialog (WPF) - runs under IronPython inside
Revit only; the rule maths lives in pymep_cobie, the Revit side in
pymep_cobie_revit.

CobieWindow(categories, rules, params, bound_names, state, load_tables)
  categories   model category names present in the document
  rules        the project's saved rules (list of dicts)
  params       per-parameter kind / type overrides
  bound_names  parameter names already bound in the project
  state        {'scope', 'bind', 'write_blanks', 'folder'} remembered
  load_tables  callable(folder) -> ({code: title}, info text)

result is None (cancelled) or {'action': 'dry' | 'apply', 'rules',
'params', 'scope', 'bind', 'write_blanks', 'folder', 'table'}."""

import io
import os

from pyrevit import forms

import pymep_cobie as C

XAML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "pymep_cobie.xaml")


def _txt(v):
    return u"{0}".format(v if v is not None else u"").strip()


class CobieWindow(forms.WPFWindow):

    def __init__(self, categories, rules, params, bound_names, state,
                 load_tables):
        forms.WPFWindow.__init__(self, XAML_PATH)
        self.result = None
        self._ready = False
        self.categories = sorted(set(_txt(c) for c in (categories or [])
                                     if _txt(c)), key=lambda s: s.lower())
        self.rules = C.clean_rules(rules)
        self.params = C.normalise_params(params)
        self.bound = set(bound_names or [])
        self._load_tables = load_tables
        self.table = {}
        self._editing = None          # index being edited, None = adding
        self._axis = "Pr"
        self._values = []             # editor working list of [name, template]
        self._codes = []              # (code, title) shown in LstCodes
        state = state or {}
        scope = state.get("scope")
        try:
            if scope == C.SCOPE_VIEW:
                self.RbScopeView.IsChecked = True
            elif scope == C.SCOPE_SELECTION:
                self.RbScopeSelection.IsChecked = True
            else:
                self.RbScopeModel.IsChecked = True
            self.ChkBind.IsChecked = bool(state.get("bind", True))
            self.ChkWriteBlanks.IsChecked = bool(state.get("write_blanks",
                                                          False))
            self.TxtTablesFolder.Text = _txt(state.get("folder"))
            for k in C.KINDS:
                self.CmbParamKind.Items.Add(k)
            for t in C.TYPES:
                self.CmbParamType.Items.Add(t)
        except Exception:
            pass
        self._fill_cats()
        self._refresh_rules()
        self._refresh_params()
        self._hide_editor()
        if _txt(self.TxtTablesFolder.Text):
            self._do_load_tables()
        self._ready = True

    # -- categories ----------------------------------------------------------
    def _fill_cats(self, ticked=None):
        from System.Windows.Controls import CheckBox
        ticked = set(c.lower() for c in (ticked or []))
        try:
            self.LstCats.Items.Clear()
            for name in self.categories:
                cb = CheckBox()
                cb.Content = name
                cb.IsChecked = name.lower() in ticked
                self.LstCats.Items.Add(cb)
        except Exception:
            pass

    def _ticked_cats(self):
        out = []
        try:
            for item in self.LstCats.Items:
                if item.IsChecked:
                    out.append(_txt(item.Content))
        except Exception:
            pass
        return out

    # -- rule list -------------------------------------------------------------
    def _refresh_rules(self, select=-1):
        try:
            self.LstRules.Items.Clear()
            for r in self.rules:
                self.LstRules.Items.Add(C.rule_label(r))
            if 0 <= select < len(self.rules):
                self.LstRules.SelectedIndex = select
        except Exception:
            pass
        self._refresh_params()

    def _selected(self):
        try:
            i = self.LstRules.SelectedIndex
        except Exception:
            return None
        if i is None or i < 0 or i >= len(self.rules):
            return None
        return i

    def on_rule_add(self, sender, args):
        self._editing = None
        self._show_editor(C.starter_rule())

    def on_rule_edit(self, sender, args):
        i = self._selected()
        if i is None:
            self.StatusText.Text = u"Pick a rule in the list first."
            return
        self._editing = i
        self._show_editor(self.rules[i])

    def on_rule_remove(self, sender, args):
        i = self._selected()
        if i is None:
            return
        del self.rules[i]
        self._hide_editor()
        self._refresh_rules(min(i, len(self.rules) - 1))

    def on_rule_up(self, sender, args):
        i = self._selected()
        if i is None or i == 0:
            return
        self.rules[i - 1], self.rules[i] = self.rules[i], self.rules[i - 1]
        self._refresh_rules(i - 1)

    def on_rule_down(self, sender, args):
        i = self._selected()
        if i is None or i >= len(self.rules) - 1:
            return
        self.rules[i + 1], self.rules[i] = self.rules[i], self.rules[i + 1]
        self._refresh_rules(i + 1)

    def on_rule_toggle(self, sender, args):
        i = self._selected()
        if i is None:
            return
        self.rules[i]["enabled"] = not self.rules[i].get("enabled", True)
        self._refresh_rules(i)

    # -- editor ----------------------------------------------------------------
    def _show_editor(self, rule):
        from System.Windows import Visibility
        r = C.normalise_rule(rule) or C.starter_rule()
        try:
            self.TxtRuleName.Text = r["name"] if r["name"] not in (
                u"(unnamed)", u"(all elements)") else u""
            self._fill_cats(r["categories"])
            self.TxtFamily.Text = r["family"]
            self.TxtType.Text = r["type"]
            self.TxtSystem.Text = r["system"]
            self.ChkRegex.IsChecked = bool(r["regex"])
            self.TxtPr.Text = r["uniclass"]["Pr"]["code"]
            self.TxtPrTitle.Text = r["uniclass"]["Pr"]["title"]
            self.TxtSs.Text = r["uniclass"]["Ss"]["code"]
            self.TxtSsTitle.Text = r["uniclass"]["Ss"]["title"]
            self.TxtEf.Text = r["uniclass"]["EF"]["code"]
            self.TxtEfTitle.Text = r["uniclass"]["EF"]["title"]
            self._values = [[k, v] for k, v in sorted(r["values"].items())]
            self._refresh_values()
            self.TxtValueParam.Text = u""
            self.TxtValueTemplate.Text = u""
            self.TxtRuleStatus.Text = u""
            self.BtnRuleSave.Content = (u"Save" if self._editing is not None
                                        else u"Add to list")
            self.PnlRuleEditor.Visibility = Visibility.Visible
        except Exception:
            pass
        self._refresh_codes()

    def _hide_editor(self):
        from System.Windows import Visibility
        try:
            self.PnlRuleEditor.Visibility = Visibility.Collapsed
        except Exception:
            pass
        self._editing = None

    def _read_editor(self):
        rule = {"name": _txt(self.TxtRuleName.Text),
                "categories": self._ticked_cats(),
                "family": _txt(self.TxtFamily.Text),
                "type": _txt(self.TxtType.Text),
                "system": _txt(self.TxtSystem.Text),
                "regex": bool(self.ChkRegex.IsChecked),
                "uniclass": {
                    "Pr": {"code": _txt(self.TxtPr.Text),
                           "title": _txt(self.TxtPrTitle.Text)},
                    "Ss": {"code": _txt(self.TxtSs.Text),
                           "title": _txt(self.TxtSsTitle.Text)},
                    "EF": {"code": _txt(self.TxtEf.Text),
                           "title": _txt(self.TxtEfTitle.Text)}},
                "values": [(k, v) for k, v in self._values],
                "enabled": True}
        if self._editing is not None and self._editing < len(self.rules):
            rule["enabled"] = self.rules[self._editing].get("enabled", True)
        for axis in C.AXES:
            code = rule["uniclass"][axis]["code"]
            if code and C.table_of(code) != axis:
                return None, (u"'{0}' is not a {1} code (expected {1}_...)."
                              .format(code, axis))
        norm = C.normalise_rule(rule)
        if norm is None:
            return None, u"A pattern is not a valid regular expression."
        if not norm["values"]:
            return None, u"Add at least one value to write (or the standard set)."
        return norm, u""

    def on_rule_save(self, sender, args):
        rule, err = self._read_editor()
        if rule is None:
            self.TxtRuleStatus.Text = err
            return
        if self._editing is not None and self._editing < len(self.rules):
            self.rules[self._editing] = rule
            sel = self._editing
        else:
            self.rules.append(rule)
            sel = len(self.rules) - 1
        self._hide_editor()
        self._refresh_rules(sel)

    def on_rule_cancel(self, sender, args):
        self._hide_editor()

    # -- values ------------------------------------------------------------------
    def _refresh_values(self, select=-1):
        try:
            self.LstValues.Items.Clear()
            for name, template in self._values:
                self.LstValues.Items.Add(u"{0}  =  {1}".format(name, template))
            if 0 <= select < len(self._values):
                self.LstValues.SelectedIndex = select
        except Exception:
            pass

    def on_value_pick(self, sender, args):
        try:
            i = self.LstValues.SelectedIndex
        except Exception:
            return
        if i is None or i < 0 or i >= len(self._values):
            return
        self.TxtValueParam.Text = self._values[i][0]
        self.TxtValueTemplate.Text = self._values[i][1]

    def on_value_set(self, sender, args):
        name = _txt(self.TxtValueParam.Text)
        template = _txt(self.TxtValueTemplate.Text)
        if not name:
            self.TxtRuleStatus.Text = u"Type the parameter name first."
            return
        for i, pair in enumerate(self._values):
            if pair[0] == name:
                pair[1] = template
                self._refresh_values(i)
                return
        self._values.append([name, template])
        self._refresh_values(len(self._values) - 1)

    def on_value_remove(self, sender, args):
        try:
            i = self.LstValues.SelectedIndex
        except Exception:
            return
        if i is None or i < 0 or i >= len(self._values):
            return
        del self._values[i]
        self._refresh_values(min(i, len(self._values) - 1))

    def on_value_defaults(self, sender, args):
        self._values = [[k, v] for k, v in C.DEFAULT_VALUES]
        self._refresh_values()

    # -- Uniclass codes --------------------------------------------------------
    def _axis_boxes(self):
        return {"Pr": (self.TxtPr, self.TxtPrTitle),
                "Ss": (self.TxtSs, self.TxtSsTitle),
                "EF": (self.TxtEf, self.TxtEfTitle)}[self._axis]

    def _find(self, axis):
        self._axis = axis
        try:
            code_box = self._axis_boxes()[0]
            self.TxtCodeSearch.Text = code_box.Text
            self.TxtCodeSearch.Focus()
        except Exception:
            pass
        self._refresh_codes()

    def on_find_pr(self, sender, args):
        self._find("Pr")

    def on_find_ss(self, sender, args):
        self._find("Ss")

    def on_find_ef(self, sender, args):
        self._find("EF")

    def on_code_search(self, sender, args):
        if getattr(self, "_ready", False):
            self._refresh_codes()

    def _refresh_codes(self):
        entries = C.codes_for_axis(self.table, self._axis)
        try:
            query = self.TxtCodeSearch.Text
        except Exception:
            query = u""
        self._codes = C.search_codes(entries, query) if entries else []
        try:
            self.LstCodes.Items.Clear()
            for code, title in self._codes:
                self.LstCodes.Items.Add(u"{0}    {1}".format(code, title))
            if not self.table:
                self.TxtCodeAxis.Text = u"no table loaded (tab 3)"
            elif not entries:
                self.TxtCodeAxis.Text = u"{0}: no codes in the loaded tables".format(
                    self._axis)
            else:
                self.TxtCodeAxis.Text = u"{0}: {1} of {2} code(s)".format(
                    self._axis, len(self._codes), len(entries))
        except Exception:
            pass

    def on_code_pick(self, sender, args):
        try:
            i = self.LstCodes.SelectedIndex
        except Exception:
            return
        if i is None or i < 0 or i >= len(self._codes):
            return
        code, title = self._codes[i]
        try:
            code_box, title_box = self._axis_boxes()
            code_box.Text = code
            title_box.Text = title
        except Exception:
            pass

    # -- parameters tab ----------------------------------------------------------
    def _param_names(self):
        return C.parameters_in([r for r in self.rules if r.get("enabled", True)])

    def _refresh_params(self, select=-1):
        names = self._param_names()
        try:
            bind = bool(self.ChkBind.IsChecked)
        except Exception:
            bind = True
        try:
            self.LstParams.Items.Clear()
            for n in names:
                if n in self.bound:
                    state = u"bound in project"
                elif bind:
                    state = u"not bound - will be created"
                else:
                    state = u"NOT bound - nothing will be written"
                self.LstParams.Items.Add(u"{0}   |   {1}   |   {2}   |   {3}".format(
                    n, C.param_kind(n, self.params), C.param_type(n, self.params),
                    state))
            if 0 <= select < len(names):
                self.LstParams.SelectedIndex = select
            self.TxtParamInfo.Text = (
                u"{0} parameter(s) written by the rules, {1} already bound in "
                u"this project.".format(len(names),
                                        len([n for n in names if n in self.bound])))
        except Exception:
            pass

    def _selected_param(self):
        names = self._param_names()
        try:
            i = self.LstParams.SelectedIndex
        except Exception:
            return None
        if i is None or i < 0 or i >= len(names):
            return None
        return names[i]

    def on_param_pick(self, sender, args):
        name = self._selected_param()
        if name is None:
            return
        try:
            self.TxtParamName.Text = name
            self.CmbParamKind.SelectedItem = C.param_kind(name, self.params)
            self.CmbParamType.SelectedItem = C.param_type(name, self.params)
        except Exception:
            pass

    def on_param_apply(self, sender, args):
        name = self._selected_param()
        if name is None:
            self.StatusText.Text = u"Pick a parameter in the list first."
            return
        try:
            kind = self.CmbParamKind.SelectedItem
            ptype = self.CmbParamType.SelectedItem
        except Exception:
            return
        entry = {}
        if kind in C.KINDS:
            entry["kind"] = kind
        if ptype in C.TYPES:
            entry["type"] = ptype
        self.params[name] = entry
        i = self._param_names().index(name)
        self._refresh_params(i)

    # -- tables tab ----------------------------------------------------------------
    def on_browse_tables(self, sender, args):
        try:
            folder = forms.pick_folder()
        except Exception:
            folder = None
        if folder:
            self.TxtTablesFolder.Text = folder
            self._do_load_tables()

    def on_load_tables(self, sender, args):
        self._do_load_tables()

    def _do_load_tables(self):
        folder = _txt(self.TxtTablesFolder.Text)
        try:
            table, info = self._load_tables(folder)
        except Exception as ex:
            table, info = {}, u"Could not read the folder: {0}".format(ex)
        self.table = table or {}
        try:
            self.TxtTablesInfo.Text = info
        except Exception:
            pass
        self._refresh_codes()

    # -- rule files ------------------------------------------------------------------
    def on_import(self, sender, args):
        path = forms.pick_file(file_ext="json")
        if not path:
            return
        try:
            text = io.open(path, "r", encoding="utf-8").read()
            rules, params = C.rules_from_json(text)
        except Exception as ex:
            self.StatusText.Text = u"Could not read the rule set: {0}".format(ex)
            return
        self.rules = rules
        self.params = params
        self._hide_editor()
        self._refresh_rules()
        self.StatusText.Text = u"Loaded {0} rule(s) from {1}.".format(
            len(rules), os.path.basename(path))

    def on_export(self, sender, args):
        path = forms.save_file(file_ext="json", default_name=C.RULES_FILENAME)
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        try:
            with io.open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(u"{0}".format(C.rules_to_json(self.rules, self.params)))
            self.StatusText.Text = u"Saved {0} rule(s) to {1}.".format(
                len(self.rules), os.path.basename(path))
        except Exception as ex:
            self.StatusText.Text = u"Could not write the rule set: {0}".format(ex)

    # -- go ------------------------------------------------------------------------------
    def _scope(self):
        try:
            if self.RbScopeView.IsChecked:
                return C.SCOPE_VIEW
            if self.RbScopeSelection.IsChecked:
                return C.SCOPE_SELECTION
        except Exception:
            pass
        return C.SCOPE_MODEL

    def _finish(self, action):
        live = [r for r in self.rules if r.get("enabled", True)]
        if not live:
            self.StatusText.Text = u"Add at least one rule (and switch it on)."
            return
        self.result = {"action": action, "rules": list(self.rules),
                       "params": dict(self.params), "scope": self._scope(),
                       "bind": bool(self.ChkBind.IsChecked),
                       "write_blanks": bool(self.ChkWriteBlanks.IsChecked),
                       "folder": _txt(self.TxtTablesFolder.Text),
                       "table": dict(self.table)}
        self.Close()

    def on_dry(self, sender, args):
        self._finish("dry")

    def on_apply(self, sender, args):
        self._finish("apply")

    def on_cancel(self, sender, args):
        self.result = None
        self.Close()
