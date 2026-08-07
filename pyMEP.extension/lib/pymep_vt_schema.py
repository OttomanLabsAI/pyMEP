# -*- coding: utf-8 -*-
"""View template / filter transfer - the JSON schema layer.

PURE PYTHON - no Revit imports, so the CPython test suite imports this
module directly. Everything Revit-flavoured lives in pymep_vt_compat /
pymep_vt_serialize / pymep_vt_deserialize; this module owns the names
both sides must agree on: the document skeleton, the evaluator
vocabulary, and the canonical JSON dump (sort_keys + indent so files
diff cleanly in git).
"""

import json

SCHEMA_VERSION = 1

# FilterStringRuleEvaluator / FilterNumericRuleEvaluator class name ->
# the schema's evaluator word (and back). One shared vocabulary for
# string, double, integer and element-id rules.
STRING_EVALUATORS = {
    "FilterStringEquals": "equals",
    "FilterStringContains": "contains",
    "FilterStringBeginsWith": "begins",
    "FilterStringEndsWith": "ends",
    "FilterStringGreater": "greater",
    "FilterStringGreaterOrEqual": "greater_or_equal",
    "FilterStringLess": "less",
    "FilterStringLessOrEqual": "less_or_equal",
}
NUMERIC_EVALUATORS = {
    "FilterNumericEquals": "equals",
    "FilterNumericGreater": "greater",
    "FilterNumericGreaterOrEqual": "greater_or_equal",
    "FilterNumericLess": "less",
    "FilterNumericLessOrEqual": "less_or_equal",
}
STRING_EVALUATOR_NAMES = dict((v, k) for k, v in STRING_EVALUATORS.items())
NUMERIC_EVALUATOR_NAMES = dict((v, k) for k, v in NUMERIC_EVALUATORS.items())

RULE_KINDS = ("string", "double", "integer", "element_id",
              "has_value", "has_no_value")
LOGIC_KINDS = ("and", "or", "rules")
PARAM_KINDS = ("builtin", "shared", "project")


def make_document(revit_version="", revit_build=""):
    """The top-level JSON skeleton. The timestamp is stamped by the
    caller (keeps this pure and testable)."""
    return {
        "schema_version": SCHEMA_VERSION,
        "revit_version": revit_version,
        "revit_build": revit_build,
        "exported": "",
        "filters": [],
        "view_templates": [],
    }


def dumps(data):
    """Canonical JSON text: sorted keys, 2-space indent, no trailing
    spaces - identical input produces byte-identical output, so two
    exports diff cleanly in git."""
    return json.dumps(data, sort_keys=True, indent=2,
                      separators=(",", ": "))


def loads(text):
    return json.loads(text)


def validate_document(data):
    """Human-readable problems with a loaded file - schema version,
    missing sections, unknown rule/logic kinds. Empty list = usable.
    Never raises."""
    notes = []
    try:
        ver = int(data.get("schema_version", -1))
    except Exception:
        ver = -1
    if ver != SCHEMA_VERSION:
        notes.append("schema_version {} (this build reads {})".format(
            data.get("schema_version"), SCHEMA_VERSION))
    if not isinstance(data.get("filters"), list):
        notes.append("no 'filters' list")
    if not isinstance(data.get("view_templates"), list):
        notes.append("no 'view_templates' list")

    def _walk(node, where):
        logic = node.get("logic")
        if logic not in LOGIC_KINDS:
            notes.append("{}: unknown logic '{}'".format(where, logic))
            return
        if logic == "rules":
            for r in node.get("rules", []):
                if r.get("rule") not in RULE_KINDS:
                    notes.append("{}: unknown rule kind '{}'".format(
                        where, r.get("rule")))
                p = r.get("parameter") or {}
                if p.get("kind") not in PARAM_KINDS:
                    notes.append("{}: unknown parameter kind '{}'".format(
                        where, p.get("kind")))
        else:
            for c in node.get("children", []):
                _walk(c, where)

    for f in data.get("filters") or []:
        if not f.get("name"):
            notes.append("a filter with no name")
            continue
        ef = f.get("element_filter")
        if ef:
            _walk(ef, "filter '{}'".format(f.get("name")))
    for t in data.get("view_templates") or []:
        if not t.get("name"):
            notes.append("a view template with no name")
    return notes


def filters_used_by(template):
    """The filter names a template dict references."""
    return [f.get("name") for f in template.get("filters", [])
            if f.get("name")]
