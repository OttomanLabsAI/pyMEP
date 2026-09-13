# -*- coding: utf-8 -*-
"""COBie / Uniclass - populate the COBie and Uniclass parameters from a
rule set, in one undo step, with a dry run first.

THE RULES (tab 1) say which elements get what: categories, a family /
type / system pattern (contains, or a regular expression), the Uniclass
Pr / Ss / EF codes, and a table of parameter -> value. Values may carry
tokens - {family} {type} {category} {mark} {system} {level} {id} {pr}
{pr_title} {ss} {ss_title} {ef} {ef_title} {uniclass} - and a new rule
starts from the conventional set: the COBie flag, COBie.Type.Category
(= "Pr code : title"), COBie.Type.Name, COBie.Type.AssetType,
COBie.Component.Name (= Mark), COBie.Component.Description, and the six
Classification.Uniclass.* number / description parameters. Rules apply
top to bottom; a later rule overrides an earlier one for the same
parameter.

PARAMETERS (tab 2): instance or type, text or yes/no, following the
Autodesk COBie Extension and NBS conventions (overridable). Missing
ones can be created as shared parameters (group 'pyMEP COBie', GUIDs
derived from the names, bound to the rules' categories under Identity
Data); one that already exists is used as it is.

UNICLASS TABLES (tab 3): CSV exports of the official tables, searched
from the rule editor by code or title words. Nothing is bundled.

SCOPE (tab 4): whole model, active view or selection.

DRY RUN prints and saves what would change (per element and parameter:
old, new, action) without touching the model. APPLY binds the missing
parameters, writes the values - instance parameters per element, type
parameters once per type with conflicts reported - and saves a CSV of
what happened. Everything runs in one TransactionGroup, so one undo.

The rule set is kept per project (project_files/cobie_rules.json in the
model's pyMEP folder) and can be exported and imported as JSON.

IronPython 2.7: .format() only, no f-strings, LF endings.
"""

__title__ = "COBie\nUniclass"
__author__ = "Glent Group"

import io
import os
import sys

for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

import clr
clr.AddReference("RevitAPI")
from Autodesk.Revit.DB import Transaction, TransactionGroup

from pyrevit import revit, forms, script

import pymep_cobie as C
import pymep_cobie_revit as R
from pymep_cobie_ui import CobieWindow
from pymep_config import load_settings, save_settings, get_export_folder
from pymep_revit import quiet, id_value

output = script.get_output()
doc = revit.doc
uidoc = revit.uidoc

ROW_CAP = 400          # table rows shown in the output window


# ---------------------------------------------------------------------------
# the project's rule set
# ---------------------------------------------------------------------------
def _rules_path():
    return os.path.join(get_export_folder(doc), "project_files", C.RULES_FILENAME)


def _load_rules():
    path = _rules_path()
    if not os.path.isfile(path):
        return [], {}
    try:
        with io.open(path, "r", encoding="utf-8") as f:
            return C.rules_from_json(f.read())
    except Exception as ex:
        output.print_md("- Could not read the saved rules ({0}); starting "
                        "empty.".format(ex))
        return [], {}


def _save_rules(rules, params):
    path = _rules_path()
    try:
        folder = os.path.dirname(path)
        if not os.path.isdir(folder):
            os.makedirs(folder)
        with io.open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(u"{0}".format(C.rules_to_json(rules, params)))
    except Exception as ex:
        output.print_md("- Could not save the rules: {0}".format(ex))


# ---------------------------------------------------------------------------
# dialog
# ---------------------------------------------------------------------------
settings = load_settings()
categories = R.model_categories(doc)
saved_rules, saved_params = _load_rules()
state = {"scope": settings.get(C.SETTINGS_SCOPE),
         "bind": settings.get(C.SETTINGS_BIND, True),
         "write_blanks": settings.get(C.SETTINGS_WRITE_BLANKS, False),
         "folder": settings.get(C.SETTINGS_TABLES_FOLDER)}

win = CobieWindow(sorted(categories.keys()), saved_rules, saved_params,
                  R.bound_parameter_names(doc), state, R.load_tables)
win.ShowDialog()
if not win.result:
    script.exit()
res = win.result
rules = [r for r in res["rules"] if r.get("enabled", True)]
params = res["params"]

_save_rules(res["rules"], params)
try:
    settings[C.SETTINGS_SCOPE] = res["scope"]
    settings[C.SETTINGS_BIND] = bool(res["bind"])
    settings[C.SETTINGS_WRITE_BLANKS] = bool(res["write_blanks"])
    if res.get("folder"):
        settings[C.SETTINGS_TABLES_FOLDER] = res["folder"]
    save_settings(settings)
except Exception:
    pass

# ---------------------------------------------------------------------------
# scope
# ---------------------------------------------------------------------------
rule_cats = set()
any_all = False
for r in rules:
    if r["categories"]:
        rule_cats.update(r["categories"])
    else:
        any_all = True
wanted = None if any_all else rule_cats
elements = R.elements_in_scope(doc, uidoc, res["scope"], wanted)
if not elements:
    forms.alert("No model elements in the chosen scope match the rules' "
                "categories.", exitscript=True)
bind_cats = (list(categories.values()) if any_all
             else [categories[n] for n in rule_cats if n in categories])

HEADER = ["Id", "Category", "Family", "Type", "Parameter", "On", "Old",
          "New", "Action", "Rule"]


# ---------------------------------------------------------------------------
# the plan: one row per element and parameter
# ---------------------------------------------------------------------------
def build_plan(will_bind):
    rows = []
    writes = []           # (Parameter, value, row index)
    type_seen = {}        # (type id, parameter) -> planned value
    counts = {"matched": 0, "set": 0, "unchanged": 0, "missing": 0,
              "readonly": 0, "conflict": 0}
    for el in elements:
        facts = R.element_facts(doc, el)
        wants = C.plan_element(facts, rules, res["write_blanks"])
        if not wants:
            continue
        counts["matched"] += 1
        base = [facts["id"], facts["category"], facts["family"], facts["type"]]
        for name in sorted(wants):
            value, rule_name = wants[name]
            where, p, holder = R.find_param(doc, el, name)
            if where is None:
                counts["missing"] += 1
                rows.append(base + [name, "-", "", value,
                                    "missing parameter" +
                                    (" (created by Apply)" if will_bind else ""),
                                    rule_name])
                continue
            if where == "readonly":
                counts["readonly"] += 1
                rows.append(base + [name, "-", R.current_text(p), value,
                                    "read-only", rule_name])
                continue
            target = R.wanted_text(p, value)
            old = R.current_text(p)
            if where == "type":
                key = (id_value(holder.Id), name)
                if key in type_seen:
                    if type_seen[key] != target:
                        counts["conflict"] += 1
                        rows.append(base + [name, "type", old, value,
                                            u"conflict: type already gets '{0}'"
                                            .format(type_seen[key]), rule_name])
                    continue
                type_seen[key] = target
            if old == target:
                counts["unchanged"] += 1
                rows.append(base + [name, where, old, value, "unchanged",
                                    rule_name])
                continue
            counts["set"] += 1
            rows.append(base + [name, where, old, value, "set", rule_name])
            writes.append((p, value, len(rows) - 1))
    return rows, writes, counts


def report(title, rows, counts, bind_rows, written=None, failed=None):
    output.print_md("### COBie / Uniclass - {0}".format(title))
    bits = ["**Elements in scope:** {0}".format(len(elements)),
            "**matched a rule:** {0}".format(counts["matched"]),
            "**set:** {0}".format(counts["set"]),
            "**unchanged:** {0}".format(counts["unchanged"]),
            "**missing parameter:** {0}".format(counts["missing"]),
            "**read-only:** {0}".format(counts["readonly"]),
            "**type conflicts:** {0}".format(counts["conflict"])]
    if written is not None:
        bits.append("**written:** {0}".format(written))
        bits.append("**failed:** {0}".format(failed))
    output.print_md("  |  ".join(bits))
    if bind_rows:
        output.print_md("**Parameters**")
        output.print_table(table_data=[list(r) for r in bind_rows],
                           columns=["Parameter", "Status", "Detail"])
    if rows:
        shown = rows[:ROW_CAP]
        output.print_table(table_data=[[u"{0}".format(c) for c in r]
                                       for r in shown], columns=HEADER)
        if len(rows) > ROW_CAP:
            output.print_md("- {0} more row(s) in the CSV.".format(
                len(rows) - ROW_CAP))
    path = R.write_report(get_export_folder(doc),
                          "cobie_" + title.split()[0].lower(), HEADER, rows)
    if path:
        output.print_md("Report: `{0}`".format(path))
    return path


# ---------------------------------------------------------------------------
# dry run
# ---------------------------------------------------------------------------
if res["action"] == "dry":
    rows, writes, counts = build_plan(res["bind"])
    path = report("dry run", rows, counts, [])
    forms.alert("Dry run - nothing changed.\n\n{0} element(s) in scope, {1} "
                "matched a rule.\n{2} value(s) would be set, {3} already "
                "right, {4} on a missing parameter, {5} read-only, {6} type "
                "conflict(s).\n\n{7}".format(
                    len(elements), counts["matched"], counts["set"],
                    counts["unchanged"], counts["missing"], counts["readonly"],
                    counts["conflict"], path or "(report not written)"),
                title="COBie / Uniclass")
    script.exit()

# ---------------------------------------------------------------------------
# apply - one undo step
# ---------------------------------------------------------------------------
bind_rows = []
tg = TransactionGroup(doc, "pyMEP: COBie / Uniclass")
tg.Start()
try:
    if res["bind"]:
        specs = [{"name": n, "kind": C.param_kind(n, params),
                  "type": C.param_type(n, params)}
                 for n in C.parameters_in(rules)]
        t = Transaction(doc, "pyMEP: COBie parameters")
        quiet(t)
        t.Start()
        try:
            bind_rows = R.ensure_parameters(doc, specs, bind_cats)
            t.Commit()
        except Exception as ex:
            t.RollBack()
            bind_rows.append(("(all)", "FAILED", u"{0}".format(ex)))
        try:
            doc.Regenerate()
        except Exception:
            pass

    rows, writes, counts = build_plan(False)
    written = 0
    failed = 0
    t = Transaction(doc, "pyMEP: COBie / Uniclass values")
    quiet(t)
    t.Start()
    try:
        for p, value, idx in writes:
            try:
                ok, why = R.write_text(p, value)
            except Exception as ex:
                ok, why = False, u"{0}".format(ex)
            if ok:
                written += 1
            else:
                failed += 1
                rows[idx][8] = u"FAILED: {0}".format(why or "Set refused")
        t.Commit()
    except Exception:
        t.RollBack()
        raise
    tg.Assimilate()
except Exception as ex:
    try:
        tg.RollBack()
    except Exception:
        pass
    forms.alert("Failed, nothing changed:\n\n{0}".format(ex), exitscript=True)

path = report("applied", rows, counts, bind_rows, written, failed)
made = [r for r in bind_rows if r[1] in ("created", "extended")]
forms.alert("COBie / Uniclass applied.\n\n{0} value(s) written on {1} "
            "element(s){2}.\n{3} already right, {4} missing parameter(s), "
            "{5} read-only, {6} type conflict(s), {7} failed.\n\n{8}".format(
                written, counts["matched"],
                ", {0} parameter(s) created or extended".format(len(made))
                if made else "",
                counts["unchanged"], counts["missing"], counts["readonly"],
                counts["conflict"], failed, path or "(report not written)"),
            title="COBie / Uniclass")
