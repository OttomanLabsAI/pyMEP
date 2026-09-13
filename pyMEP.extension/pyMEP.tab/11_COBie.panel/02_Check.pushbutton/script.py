# -*- coding: utf-8 -*-
"""COBie Check - the state of the COBie data in this model, read only.

Every model element whose category carries the COBie parameter is
listed with its flag (yes / no / unset), Mark, Uniclass Pr and Ss
codes. The report gives the counts per category and the issues:
flagged elements without a Mark, without a Pr code, without an Ss
code, and Marks shared by several flagged elements. A CSV of the
elements and one of the issues go to the model's pyMEP folder.

Needs a bound COBie parameter - run COBie / Uniclass with 'create and
bind' first when there is none.

IronPython 2.7: .format() only, no f-strings, LF endings.
"""

__title__ = "COBie\nCheck"
__author__ = "Glent Group"

import sys

for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

from pyrevit import revit, forms, script

import pymep_cobie as C
import pymep_cobie_revit as R
from pymep_config import get_export_folder
from pymep_revit import make_id

output = script.get_output()
doc = revit.doc
uidoc = revit.uidoc

PR = "Classification.Uniclass.Pr.Number"
SS = "Classification.Uniclass.Ss.Number"
ISSUE_CAP = 500

bound = R.bound_parameter_names(doc)
if "COBie" not in bound:
    forms.alert("No 'COBie' parameter is bound in this project, so there is "
                "nothing to check.\n\nRun COBie / Uniclass with 'create and "
                "bind the parameters that are missing' ticked first.",
                exitscript=True)

rows = []
per_cat = {}
for el in R.elements_in_scope(doc, uidoc, C.SCOPE_MODEL, None):
    p = R.read_param(doc, el, "COBie")
    if p is None:
        continue
    facts = R.element_facts(doc, el)
    flag_text = R.current_text(p)
    flag = None
    if flag_text == "1":
        flag = True
    elif flag_text == "0":
        flag = False
    pr_p = R.read_param(doc, el, PR)
    ss_p = R.read_param(doc, el, SS)
    row = {"id": facts["id"], "category": facts["category"],
           "family": facts["family"], "type": facts["type"],
           "mark": facts["mark"], "cobie": flag,
           "pr": R.current_text(pr_p) if pr_p is not None else u"",
           "ss": R.current_text(ss_p) if ss_p is not None else u""}
    rows.append(row)
    c = per_cat.setdefault(facts["category"], {"yes": 0, "no": 0, "unset": 0})
    c["yes" if flag is True else "no" if flag is False else "unset"] += 1

issues, summary = C.check_rows(rows)

output.print_md("### COBie Check")
output.print_md("**Elements with a COBie parameter:** {0}  |  **flagged:** {1}  |  "
                "**not flagged:** {2}  |  **unset:** {3}  |  **issues:** {4}  |  "
                "**Pr parameter bound:** {5}  |  **Ss parameter bound:** {6}".format(
                    summary["elements"], summary["cobie_yes"], summary["cobie_no"],
                    summary["cobie_unset"], summary["issues"],
                    "yes" if PR in bound else "NO",
                    "yes" if SS in bound else "NO"))
output.print_table(
    table_data=[[cat, c["yes"], c["no"], c["unset"]]
                for cat, c in sorted(per_cat.items())],
    columns=["Category", "COBie yes", "COBie no", "unset"])

by_id = dict((r["id"], r) for r in rows)
if issues:
    output.print_md("**Issues**")
    shown = issues[:ISSUE_CAP]
    table = []
    for eid, text in shown:
        r = by_id.get(eid, {})
        try:
            link = output.linkify(make_id(eid))
        except Exception:
            link = u"{0}".format(eid)
        table.append([link, r.get("category", u""), r.get("family", u""),
                      r.get("type", u""), r.get("mark", u""), text])
    output.print_table(table_data=table,
                       columns=["Id", "Category", "Family", "Type", "Mark",
                                "Issue"])
    if len(issues) > ISSUE_CAP:
        output.print_md("- {0} more issue(s) in the CSV.".format(
            len(issues) - ISSUE_CAP))
else:
    output.print_md("No issues found.")

folder = get_export_folder(doc)
p1 = R.write_report(folder, "cobie_check_elements",
                    ["Id", "Category", "Family", "Type", "Mark", "COBie", "Pr", "Ss"],
                    [[r["id"], r["category"], r["family"], r["type"], r["mark"],
                      "yes" if r["cobie"] is True else "no" if r["cobie"] is False
                      else "", r["pr"], r["ss"]] for r in rows])
p2 = R.write_report(folder, "cobie_check_issues", ["Id", "Issue"],
                    [[i, t] for i, t in issues])
for p in (p1, p2):
    if p:
        output.print_md("Report: `{0}`".format(p))

forms.alert("COBie Check\n\n{0} element(s) carry the COBie parameter: {1} "
            "flagged, {2} not, {3} unset.\n{4} issue(s) - see the output "
            "window and the CSV reports.".format(
                summary["elements"], summary["cobie_yes"], summary["cobie_no"],
                summary["cobie_unset"], summary["issues"]),
            title="COBie Check")
