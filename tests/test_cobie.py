#!/usr/bin/env python3
"""Unit tests for pymep_cobie - the pure half of the COBie / Uniclass
parameter population (rules, tokens, parameter kinds, Uniclass tables,
rule-set files, checks).

Run:  python3 tests/test_cobie.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..",
    "pyMEP.extension", "lib"))

import pymep_cobie as C


PIPE = {"category": "Pipes", "family": "Pipe Types", "type": "PE SDR11",
        "system": "Storm Drainage", "mark": "P12", "level": "LVL 0.00",
        "id": 4711}
CHAMBER = {"category": "Plumbing Fixtures", "family": "MH_1200",
           "type": "1200x1200", "system": "", "mark": "LV1/Z1", "id": 99}


class Rules(unittest.TestCase):

    def test_normalise(self):
        r = C.normalise_rule({"name": " Pipes ", "categories": ["Pipes", "",
                                                                "Pipes"],
                              "family": "pe ", "uniclass": {"Pr": "Pr_65",
                                                            "Ss": {"code": "Ss_50", "title": "Drainage"}},
                              "values": [("COBie", "Yes"), ("", "x")]})
        self.assertEqual(r["name"], "Pipes")
        self.assertEqual(r["categories"], ["Pipes"])
        self.assertEqual(r["family"], "pe")
        self.assertEqual(r["uniclass"]["Pr"], {"code": "Pr_65", "title": ""})
        self.assertEqual(r["uniclass"]["Ss"]["title"], "Drainage")
        self.assertEqual(r["uniclass"]["EF"], {"code": "", "title": ""})
        self.assertEqual(r["values"], {"COBie": "Yes"})
        self.assertTrue(r["enabled"])
        self.assertIsNone(C.normalise_rule("nope"))
        # a broken regex is refused when regex mode is on, kept as text otherwise
        self.assertIsNone(C.normalise_rule({"family": "(", "regex": True}))
        self.assertEqual(C.normalise_rule({"family": "("})["family"], "(")
        self.assertEqual(C.normalise_rule({})["name"], "(all elements)")

    def test_matching(self):
        r = {"categories": ["Pipes"], "family": "pipe", "system": "storm"}
        self.assertTrue(C.rule_matches(r, PIPE))
        self.assertFalse(C.rule_matches(r, CHAMBER))
        self.assertFalse(C.rule_matches(dict(r, system="foul"), PIPE))
        self.assertTrue(C.rule_matches({"categories": ["pipes"]}, PIPE))
        self.assertTrue(C.rule_matches({}, PIPE))
        self.assertFalse(C.rule_matches({"enabled": False}, PIPE))
        rx = {"type": r"^PE\s+SDR\d+$", "regex": True}
        self.assertTrue(C.rule_matches(rx, PIPE))
        self.assertFalse(C.rule_matches(dict(rx, type=r"^PVC"), PIPE))

    def test_expand_tokens(self):
        r = C.normalise_rule({"uniclass": {"Pr": {"code": "Pr_65_52",
                                                  "title": "Pipes"}}})
        self.assertEqual(C.expand("{family} : {type}", PIPE, r),
                         "Pipe Types : PE SDR11")
        self.assertEqual(C.expand("{uniclass}", PIPE, r), "Pr_65_52 : Pipes")
        self.assertEqual(C.expand("{pr}/{ss}/{mark}/{id}", PIPE, r),
                         "Pr_65_52//P12/4711")
        self.assertEqual(C.expand("{unknown}", PIPE, r), "{unknown}")
        self.assertEqual(C.expand("{uniclass}", PIPE, {"uniclass": {"Pr": "Pr_1"}}),
                         "Pr_1")
        self.assertEqual(C.expand("  Fixed ", PIPE), "Fixed")

    def test_plan_layers_rules_in_order(self):
        base = {"name": "all pipes", "categories": ["Pipes"],
                "uniclass": {"Ss": {"code": "Ss_50", "title": "Drain"}},
                "values": {"COBie": "Yes",
                           "Classification.Uniclass.Ss.Number": "{ss}",
                           "Classification.Uniclass.Pr.Number": "{pr}"}}
        pe = {"name": "PE pipes", "family": "Pipe", "type": "PE",
              "uniclass": {"Pr": "Pr_65_52_63"},
              "values": {"Classification.Uniclass.Pr.Number": "{pr}",
                         "COBie.Type.Name": "{family} : {type}"}}
        plan = C.plan_element(PIPE, [base, pe])
        self.assertEqual(plan["COBie"], ("Yes", "all pipes"))
        self.assertEqual(plan["Classification.Uniclass.Ss.Number"],
                         ("Ss_50", "all pipes"))
        # the later rule overrides the earlier blank Pr
        self.assertEqual(plan["Classification.Uniclass.Pr.Number"],
                         ("Pr_65_52_63", "PE pipes"))
        self.assertEqual(plan["COBie.Type.Name"],
                         ("Pipe Types : PE SDR11", "PE pipes"))
        # the chamber matches neither
        self.assertEqual(C.plan_element(CHAMBER, [base, pe]), {})
        # blanks are dropped unless asked for
        self.assertNotIn("Classification.Uniclass.Pr.Number",
                         C.plan_element(PIPE, [base]))
        self.assertEqual(C.plan_element(PIPE, [base], write_blanks=True)
                         ["Classification.Uniclass.Pr.Number"], ("", "all pipes"))

    def test_starter_and_label(self):
        r = C.starter_rule("Chambers", ["Plumbing Fixtures"])
        self.assertEqual(len(r["values"]), len(C.DEFAULT_VALUES))
        self.assertEqual(r["values"]["COBie"], "Yes")
        lab = C.rule_label(r)
        self.assertIn("Chambers", lab)
        self.assertIn("Plumbing Fixtures", lab)
        self.assertIn("12 value(s)", lab)
        self.assertIn("(off)", C.rule_label(dict(r, enabled=False)))
        self.assertEqual(C.rule_label(None), "(invalid rule)")
        self.assertEqual(C.parameters_in([r])[:2],
                         ["COBie", "COBie.Type.Category"])


class Parameters(unittest.TestCase):

    def test_kind_and_type_conventions(self):
        self.assertEqual(C.param_kind("COBie"), "instance")
        self.assertEqual(C.param_type("COBie"), "yesno")
        self.assertEqual(C.param_kind("COBie.Type.Category"), "type")
        self.assertEqual(C.param_kind("COBie.Component.Name"), "instance")
        self.assertEqual(C.param_kind("Classification.Uniclass.Pr.Number"), "type")
        self.assertEqual(C.param_kind("Classification.Uniclass.Ss.Number"),
                         "instance")
        self.assertEqual(C.param_kind("Classification.Uniclass.EF.Number"), "type")
        self.assertEqual(C.param_type("COBie.Type.Name"), "text")
        self.assertEqual(C.param_kind("Anything Else"), "instance")

    def test_overrides(self):
        ov = {"COBie": {"kind": "type"},
              "Classification.Uniclass.Ss.Number": {"kind": "type", "type": "text"},
              "junk": "no"}
        self.assertEqual(C.param_kind("COBie", ov), "type")
        self.assertEqual(C.param_type("COBie", ov), "yesno")
        self.assertEqual(C.param_kind("Classification.Uniclass.Ss.Number", ov),
                         "type")
        self.assertEqual(C.normalise_params(ov),
                         {"COBie": {"kind": "type"},
                          "Classification.Uniclass.Ss.Number":
                              {"kind": "type", "type": "text"}})
        self.assertEqual(C.normalise_params({"x": {"kind": "odd"}}), {})

    def test_guid_is_stable(self):
        a = C.param_guid("COBie.Type.Name")
        self.assertEqual(a, C.param_guid(" cobie.type.name "))
        self.assertNotEqual(a, C.param_guid("COBie.Type.Category"))
        self.assertEqual(len(a), 36)

    def test_yesno(self):
        self.assertEqual(C.to_yesno("Yes"), 1)
        self.assertEqual(C.to_yesno("no"), 0)
        self.assertEqual(C.to_yesno("1"), 1)
        self.assertEqual(C.to_yesno("TRUE"), 1)
        self.assertIsNone(C.to_yesno("maybe"))
        self.assertIsNone(C.to_yesno(""))


class RuleFiles(unittest.TestCase):

    def test_round_trip(self):
        rules = [C.starter_rule("Pipes", ["Pipes"])]
        text = C.rules_to_json(rules, {"COBie": {"kind": "type"}})
        back, params = C.rules_from_json(text)
        self.assertEqual(back, rules)
        self.assertEqual(params, {"COBie": {"kind": "type"}})
        self.assertIn('"kind": "pymep-cobie-rules"', text)

    def test_rejects_other_files(self):
        with self.assertRaises(ValueError):
            C.rules_from_json("{not json")
        with self.assertRaises(ValueError):
            C.rules_from_json('{"kind": "pymep-drainage-edits"}')


class UniclassTables(unittest.TestCase):

    CSV = (u"﻿Code,Group,Sub group,Section,Object,Title,NBS Code\n"
           u"Pr,,,,,Products,\n"
           u"Pr_65,65,,,,\"Pipes, ducts and cables\",\n"
           u"Pr_65_52,65,52,,,Pipework,\n"
           u"Pr_65_52_63,65,52,63,,Polyethylene pipes,\n"
           u"junk,,,,,Not a code,\n")

    def test_parse(self):
        got = C.parse_table_csv(self.CSV)
        self.assertEqual(got, [("Pr_65", "Pipes, ducts and cables"),
                               ("Pr_65_52", "Pipework"),
                               ("Pr_65_52_63", "Polyethylene pipes")])
        self.assertEqual(C.parse_table_csv(self.CSV.encode("utf-8")), got)
        self.assertEqual(C.parse_table_csv(u"a,b\n1,2\n"), [])
        # a header further down, lower-case, still found
        late = u"Uniclass 2015 - Pr\n\ncode,title\nSs_50,Drainage systems\n"
        self.assertEqual(C.parse_table_csv(late), [("Ss_50", "Drainage systems")])

    def test_table_of_and_search(self):
        self.assertEqual(C.table_of("Pr_65_52_63"), "Pr")
        self.assertEqual(C.table_of("ss_50"), "Ss")
        self.assertEqual(C.table_of("EF_50_30"), "EF")
        self.assertEqual(C.table_of("junk"), "")
        table = C.merge_tables([C.parse_table_csv(self.CSV),
                                [("Ss_50", "Drainage systems"),
                                 ("Pr_65", "older title")]])
        self.assertEqual(table["Pr_65"], "Pipes, ducts and cables")
        self.assertEqual(C.codes_for_axis(table, "Ss"),
                         [("Ss_50", "Drainage systems")])
        entries = C.codes_for_axis(table, "Pr")
        self.assertEqual([c for c, _ in C.search_codes(entries, "pr_65_52")],
                         ["Pr_65_52", "Pr_65_52_63"])
        self.assertEqual([c for c, _ in C.search_codes(entries, "polyethylene pipes")],
                         ["Pr_65_52_63"])
        self.assertEqual(len(C.search_codes(entries, "")), 3)
        self.assertEqual(len(C.search_codes(entries, "", limit=2)), 2)
        self.assertEqual(C.title_for(table, "Pr_65_52"), "Pipework")
        self.assertEqual(C.title_for(table, "nope"), "")


class Checks(unittest.TestCase):

    def test_issues(self):
        rows = [
            {"id": 1, "mark": "A", "cobie": True, "pr": "Pr_1", "ss": "Ss_1"},
            {"id": 2, "mark": "A", "cobie": True, "pr": "Pr_1", "ss": "Ss_1"},
            {"id": 3, "mark": "", "cobie": True, "pr": "", "ss": "Ss_1"},
            {"id": 4, "mark": "B", "cobie": False, "pr": "", "ss": ""},
            {"id": 5, "mark": "C", "cobie": None, "pr": "", "ss": ""},
        ]
        issues, summary = C.check_rows(rows)
        texts = [t for _, t in issues]
        self.assertEqual(summary, {"elements": 5, "cobie_yes": 3, "cobie_no": 1,
                                   "cobie_unset": 1, "issues": 4})
        self.assertEqual(len([t for t in texts if "shared by 2" in t]), 2)
        self.assertIn((3, "COBie element without a Mark"), issues)
        self.assertIn((3, "COBie element without a Uniclass Pr code"), issues)
        self.assertEqual(C.check_rows([]), ([], {"elements": 0, "cobie_yes": 0,
                                                 "cobie_no": 0, "cobie_unset": 0,
                                                 "issues": 0}))


if __name__ == "__main__":
    unittest.main()
