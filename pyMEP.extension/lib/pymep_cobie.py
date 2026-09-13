# -*- coding: utf-8 -*-
"""COBie / Uniclass parameter population - PURE PYTHON (no Revit or WPF
imports) so the CPython suite exercises it. The COBie panel's buttons do
the Revit side: collecting facts about each element, binding shared
parameters, writing values.

THE MODEL
  * A RULE says which elements it is for - categories, a family-name
    pattern, a type-name pattern, a system pattern (plain substring or
    regex, case-insensitive) - and what to write: Uniclass codes on the
    Pr / Ss / EF axes and a table of parameter -> value template.
  * Every matching rule applies, in list order; a later rule overrides an
    earlier one for the same parameter. So "all pipes get this Ss code"
    can sit above "this family gets that Pr code".
  * A value TEMPLATE may carry tokens: {family} {type} {category} {mark}
    {system} {level} {id} {pr} {pr_title} {ss} {ss_title} {ef} {ef_title}
    and {uniclass} (= "{pr} : {pr_title}"). A template that expands to
    blank is skipped unless write_blanks is on.
  * PARAMETER KIND (instance / type) and TYPE (text / yesno) follow the
    conventional names - COBie.Type.* and Classification.Uniclass.Pr/EF.*
    on the type, COBie, COBie.Component.* and Classification.Uniclass.Ss.*
    on the instance - and can be overridden per parameter.
  * UNICLASS TABLES are read from CSV exports of the official tables (a
    'Code' column and a 'Title' column, anything else ignored); the table
    letter is the code's prefix (Pr_, Ss_, EF_, ...). Nothing is bundled:
    a wrong code in a deliverable is worse than none.
  * A shared parameter created by pyMEP gets a GUID derived from its
    name, so the same parameter has the same GUID in every project.

The rule set travels as JSON ({"kind": "pymep-cobie-rules"}) so it can be
kept per project and shared between offices.
"""

import csv
import io
import json
import re
import uuid

RULES_FILENAME = "cobie_rules.json"
RULES_KIND = "pymep-cobie-rules"
RULES_VERSION = 1

SETTINGS_TABLES_FOLDER = "cobie_uniclass_folder"
SETTINGS_SCOPE = "cobie_scope"
SETTINGS_BIND = "cobie_bind_missing"
SETTINGS_WRITE_BLANKS = "cobie_write_blanks"

SCOPE_MODEL = "model"
SCOPE_VIEW = "view"
SCOPE_SELECTION = "selection"
SCOPES = (SCOPE_MODEL, SCOPE_VIEW, SCOPE_SELECTION)

AXES = ("Pr", "Ss", "EF")
TOKENS = ("family", "type", "category", "mark", "system", "level", "id",
          "pr", "pr_title", "ss", "ss_title", "ef", "ef_title", "uniclass")

PARAM_GROUP = "pyMEP COBie"          # group name in the shared parameter file
_GUID_NAMESPACE = uuid.UUID("5a2c1f4e-9b7d-4c3a-8e61-0d2f7b9c4a15")

# The conventional parameter names (Autodesk COBie Extension for Revit and
# the NBS / Classification Manager Uniclass set) with the value template a
# new rule starts from.
DEFAULT_VALUES = (
    (u"COBie", u"Yes"),
    (u"COBie.Type.Category", u"{uniclass}"),
    (u"COBie.Type.Name", u"{family} : {type}"),
    (u"COBie.Type.AssetType", u"Fixed"),
    (u"COBie.Component.Name", u"{mark}"),
    (u"COBie.Component.Description", u"{type}"),
    (u"Classification.Uniclass.Pr.Number", u"{pr}"),
    (u"Classification.Uniclass.Pr.Description", u"{pr_title}"),
    (u"Classification.Uniclass.Ss.Number", u"{ss}"),
    (u"Classification.Uniclass.Ss.Description", u"{ss_title}"),
    (u"Classification.Uniclass.EF.Number", u"{ef}"),
    (u"Classification.Uniclass.EF.Description", u"{ef_title}"),
)

# (name prefix, kind, type) - first match wins; unknown names are
# instance text parameters.
_KIND_TABLE = (
    (u"cobie.type.", "type", "text"),
    (u"cobie.component.", "instance", "text"),
    (u"cobie", "instance", "yesno"),
    (u"classification.uniclass.ss.", "instance", "text"),
    (u"classification.uniclass.", "type", "text"),
    (u"classification.", "type", "text"),
)

KINDS = ("instance", "type")
TYPES = ("text", "yesno")


# ---------------------------------------------------------------------------
# text helpers
# ---------------------------------------------------------------------------
def _text(v):
    if v is None:
        return u""
    return u"{0}".format(v).strip()


def to_yesno(text):
    """'Yes' / 'No' / '1' / '0' / 'true' / 'false' -> 1 / 0; None when
    the text is neither."""
    t = _text(text).lower()
    if t in (u"yes", u"y", u"1", u"true", u"on", u"x"):
        return 1
    if t in (u"no", u"n", u"0", u"false", u"off", u""):
        return 0 if t else None
    return None


# ---------------------------------------------------------------------------
# parameters
# ---------------------------------------------------------------------------
def _lookup_kind(name):
    low = _text(name).lower()
    for prefix, kind, ptype in _KIND_TABLE:
        if low == prefix.rstrip(u".") or low.startswith(prefix):
            return kind, ptype
    return "instance", "text"


def param_kind(name, overrides=None):
    """'instance' or 'type' for a parameter name (overrides win)."""
    ov = (overrides or {}).get(_text(name)) or {}
    k = ov.get("kind")
    if k in KINDS:
        return k
    return _lookup_kind(name)[0]


def param_type(name, overrides=None):
    """'text' or 'yesno' for a parameter name (overrides win)."""
    ov = (overrides or {}).get(_text(name)) or {}
    t = ov.get("type")
    if t in TYPES:
        return t
    return _lookup_kind(name)[1]


def param_guid(name):
    """A stable GUID for a parameter name (case-insensitive), so a
    parameter pyMEP creates has the same GUID in every project."""
    return str(uuid.uuid5(_GUID_NAMESPACE, _text(name).lower()))


def normalise_params(raw):
    """{name: {'kind', 'type'}} cleaned; unknown kinds / types dropped."""
    out = {}
    if not isinstance(raw, dict):
        return out
    for name, spec in raw.items():
        key = _text(name)
        if not key or not isinstance(spec, dict):
            continue
        entry = {}
        if spec.get("kind") in KINDS:
            entry["kind"] = spec["kind"]
        if spec.get("type") in TYPES:
            entry["type"] = spec["type"]
        if entry:
            out[key] = entry
    return out


def parameters_in(rules):
    """Every parameter name the rules write, first appearance first."""
    seen, out = set(), []
    for r in rules or []:
        for name in (r.get("values") or {}):
            if name not in seen:
                seen.add(name)
                out.append(name)
    return out


# ---------------------------------------------------------------------------
# rules
# ---------------------------------------------------------------------------
def _pattern_ok(pattern, regex):
    if not regex or not pattern:
        return True
    try:
        re.compile(pattern)
        return True
    except re.error:
        return False


def normalise_rule(rule):
    """A rule dict cleaned, or None when it is unusable."""
    if not isinstance(rule, dict):
        return None
    name = _text(rule.get("name"))
    cats = []
    for c in rule.get("categories") or []:
        c = _text(c)
        if c and c not in cats:
            cats.append(c)
    regex = bool(rule.get("regex", False))
    pats = {}
    for key in ("family", "type", "system"):
        pats[key] = _text(rule.get(key))
        if not _pattern_ok(pats[key], regex):
            return None
    uni = {}
    raw_uni = rule.get("uniclass") or {}
    for axis in AXES:
        one = raw_uni.get(axis) if isinstance(raw_uni, dict) else None
        if isinstance(one, dict):
            code, title = _text(one.get("code")), _text(one.get("title"))
        else:
            code, title = _text(one), u""
        uni[axis] = {"code": code, "title": title}
    values = {}
    raw_vals = rule.get("values")
    if isinstance(raw_vals, dict):
        items = raw_vals.items()
    elif isinstance(raw_vals, (list, tuple)):
        items = [(a, b) for a, b in raw_vals]
    else:
        items = []
    for k, v in items:
        k = _text(k)
        if k:
            values[k] = _text(v)
    if not name and not cats and not any(pats.values()):
        name = u"(all elements)"
    return {"name": name or u"(unnamed)", "categories": cats,
            "family": pats["family"], "type": pats["type"],
            "system": pats["system"], "regex": regex,
            "uniclass": uni, "values": values,
            "enabled": bool(rule.get("enabled", True))}


def starter_rule(name=u"", categories=None):
    """A new rule with the conventional parameter set filled in."""
    return normalise_rule({"name": name, "categories": categories or [],
                           "values": list(DEFAULT_VALUES)})


def rule_label(rule):
    r = normalise_rule(rule)
    if r is None:
        return u"(invalid rule)"
    where = []
    if r["categories"]:
        where.append(u", ".join(r["categories"][:3]) +
                     (u" +{0}".format(len(r["categories"]) - 3)
                      if len(r["categories"]) > 3 else u""))
    for key, tag in (("family", u"family"), ("type", u"type"),
                     ("system", u"system")):
        if r[key]:
            where.append(u"{0}~{1}".format(tag, r[key]))
    codes = [r["uniclass"][a]["code"] for a in AXES if r["uniclass"][a]["code"]]
    bits = [r["name"]]
    if where:
        bits.append(u"[" + u"; ".join(where) + u"]")
    if codes:
        bits.append(u" ".join(codes))
    bits.append(u"{0} value(s)".format(len(r["values"])))
    if not r["enabled"]:
        bits.append(u"(off)")
    return u"  ".join(bits)


def _matches(pattern, text, regex):
    if not pattern:
        return True
    text = _text(text)
    if regex:
        try:
            return re.search(pattern, text, re.IGNORECASE) is not None
        except re.error:
            return False
    return pattern.lower() in text.lower()


def rule_matches(rule, facts):
    """Does the rule apply to an element described by facts
    {category, family, type, system, ...}?"""
    r = normalise_rule(rule)
    if r is None or not r["enabled"]:
        return False
    facts = facts or {}
    if r["categories"]:
        cat = _text(facts.get("category")).lower()
        if cat not in [c.lower() for c in r["categories"]]:
            return False
    for key in ("family", "type", "system"):
        if not _matches(r[key], facts.get(key), r["regex"]):
            return False
    return True


def expand(template, facts, rule=None):
    """The template with its tokens filled from facts and the rule's
    Uniclass codes. Unknown tokens stay as typed."""
    facts = facts or {}
    r = normalise_rule(rule) if rule else None
    uni = r["uniclass"] if r else dict((a, {"code": u"", "title": u""})
                                       for a in AXES)
    values = {
        "family": _text(facts.get("family")),
        "type": _text(facts.get("type")),
        "category": _text(facts.get("category")),
        "mark": _text(facts.get("mark")),
        "system": _text(facts.get("system")),
        "level": _text(facts.get("level")),
        "id": _text(facts.get("id")),
        "pr": uni["Pr"]["code"], "pr_title": uni["Pr"]["title"],
        "ss": uni["Ss"]["code"], "ss_title": uni["Ss"]["title"],
        "ef": uni["EF"]["code"], "ef_title": uni["EF"]["title"],
    }
    values["uniclass"] = (u"{0} : {1}".format(values["pr"], values["pr_title"])
                          if values["pr"] and values["pr_title"]
                          else values["pr"])
    out = _text(template)
    for tok in TOKENS:
        out = out.replace(u"{" + tok + u"}", values[tok])
    return out.strip()


def plan_element(facts, rules, write_blanks=False):
    """{parameter: (value text, rule name)} for one element: every
    matching rule in order, later rules overriding earlier ones. Blank
    expansions are dropped unless write_blanks."""
    out = {}
    for raw in rules or []:
        r = normalise_rule(raw)
        if r is None or not rule_matches(r, facts):
            continue
        for name, template in r["values"].items():
            value = expand(template, facts, r)
            if not value and not write_blanks:
                continue
            out[name] = (value, r["name"])
    return out


def clean_rules(raw):
    out = []
    for r in raw or []:
        n = normalise_rule(r)
        if n is not None:
            out.append(n)
    return out


# ---------------------------------------------------------------------------
# rule sets on disk
# ---------------------------------------------------------------------------
def rules_to_json(rules, params=None):
    doc = {"kind": RULES_KIND, "version": RULES_VERSION,
           "rules": clean_rules(rules), "params": normalise_params(params)}
    return json.dumps(doc, indent=2, sort_keys=True)


def rules_from_json(text):
    """(rules, params) from a rule-set file; raises ValueError when the
    text is not a pyMEP COBie rule set."""
    try:
        data = json.loads(text)
    except Exception as ex:
        raise ValueError(u"not JSON: {0}".format(ex))
    if not isinstance(data, dict) or data.get("kind") != RULES_KIND:
        raise ValueError(u"not a pyMEP COBie rule set (kind must be '{0}')"
                         .format(RULES_KIND))
    return clean_rules(data.get("rules")), normalise_params(data.get("params"))


# ---------------------------------------------------------------------------
# Uniclass tables
# ---------------------------------------------------------------------------
_CODE_RE = re.compile(r"^([A-Za-z]{2})_[0-9A-Za-z_]+$")


def table_of(code):
    """'Pr_65_52_63' -> 'Pr'; '' when the text is not a Uniclass code."""
    m = _CODE_RE.match(_text(code))
    return m.group(1)[0].upper() + m.group(1)[1:] if m else u""


def parse_table_csv(text):
    """[(code, title), ...] from a CSV export of a Uniclass table. The
    header row is the first row holding a 'code' column and a 'title'
    column (case-insensitive); other columns are ignored. Rows whose
    code is not shaped like a Uniclass code are dropped."""
    if isinstance(text, bytes):
        text = text.decode("utf-8-sig", "replace")
    text = text.lstrip(u"﻿")
    rows = list(csv.reader(io.StringIO(text)))
    code_i = title_i = None
    start = 0
    for i, row in enumerate(rows):
        low = [_text(c).lower() for c in row]
        if u"code" in low and u"title" in low:
            code_i, title_i = low.index(u"code"), low.index(u"title")
            start = i + 1
            break
    if code_i is None:
        return []
    out = []
    for row in rows[start:]:
        if len(row) <= max(code_i, title_i):
            continue
        code, title = _text(row[code_i]), _text(row[title_i])
        if table_of(code):
            out.append((code, title))
    return out


def merge_tables(entry_lists):
    """One {code: title} from several parsed tables (first title wins)."""
    out = {}
    for entries in entry_lists or []:
        for code, title in entries:
            if code not in out:
                out[code] = title
    return out


def codes_for_axis(table, axis):
    """Sorted (code, title) pairs of one axis from a merged table."""
    return sorted((c, t) for c, t in (table or {}).items()
                  if table_of(c) == axis)


def search_codes(entries, query, limit=60):
    """Entries whose code starts with the query or whose title holds
    every word of it (case-insensitive), in code order, capped."""
    q = _text(query).lower()
    words = [w for w in re.split(r"\s+", q) if w]
    out = []
    for code, title in sorted(entries or []):
        lc, lt = code.lower(), title.lower()
        if not words or lc.startswith(q) or all(w in lt or w in lc
                                                 for w in words):
            out.append((code, title))
            if len(out) >= limit:
                break
    return out


def title_for(table, code):
    return _text((table or {}).get(_text(code), u""))


# ---------------------------------------------------------------------------
# checks
# ---------------------------------------------------------------------------
def check_rows(rows):
    """Issues over rows [{id, category, family, type, mark, cobie,
    pr, ss}]: cobie True/False/None, codes as text. Returns
    (issues [(id, text)], summary dict)."""
    issues = []
    rows = rows or []
    flagged = [r for r in rows if r.get("cobie") is True]
    marks = {}
    for r in flagged:
        m = _text(r.get("mark"))
        if m:
            marks.setdefault(m, []).append(r.get("id"))
        else:
            issues.append((r.get("id"), u"COBie element without a Mark"))
        if not _text(r.get("pr")):
            issues.append((r.get("id"), u"COBie element without a Uniclass "
                                        u"Pr code"))
        if not _text(r.get("ss")):
            issues.append((r.get("id"), u"COBie element without a Uniclass "
                                        u"Ss code"))
    for m, ids in sorted(marks.items()):
        if len(ids) > 1:
            for i in ids:
                issues.append((i, u"Mark '{0}' shared by {1} COBie "
                                  u"elements".format(m, len(ids))))
    summary = {
        "elements": len(rows),
        "cobie_yes": len(flagged),
        "cobie_no": len([r for r in rows if r.get("cobie") is False]),
        "cobie_unset": len([r for r in rows if r.get("cobie") is None]),
        "issues": len(issues),
    }
    return issues, summary
