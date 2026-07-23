# -*- coding: utf-8 -*-
"""Export the model's pipe types to a versioned, round-trip-ready JSON.

The export captures everything a companion *Import Pipe Types* button
(possibly running in an OLDER Revit) needs to rebuild the types:

  - every selected ``PipeType``: name, useful type parameters, preferred
    junction type, and the full routing-preference rule table
    (Segments / Elbows / Junctions / ... via ``RoutingPreferenceManager``);
  - a deduplicated top-level ``segments`` section: each referenced
    ``PipeSegment`` with roughness, schedule type, material and its full
    ``MEPSize`` catalogue;
  - a deduplicated top-level ``fittings`` section: every referenced
    fitting family/type identity plus how many rules use it.

Because ElementIds do not survive across models (let alone Revit
versions), NOTHING in the JSON is an ElementId - every reference is by
stable name: family name, type name, segment name, schedule name,
material name. All lengths are millimetres (converted with the repo's
``ft2mm``); the header carries ``schema_version`` so the import side can
evolve safely.

Layout of this module: the *pure* data-shaping functions at the top take
plain values and build the schema (they are unit-tested under CPython by
``tests/test_pipetypes_export.py`` - keep them stdlib-only); the Revit
API access below feeds them.

IronPython 2.7 / Revit 2021-2026 safe. Rule-group names and criterion
classes are resolved defensively (``getattr`` / ``isinstance``) so a
Revit build missing one enum member degrades to a warning, never a
crash.
"""

import clr
clr.AddReference("RevitAPI")

import datetime
import json
import os

from Autodesk.Revit.DB import (
    FilteredElementCollector, BuiltInParameter, ElementId, FamilySymbol,
    SaveAsOptions,
)
from Autodesk.Revit.DB import RoutingPreferenceRuleGroupType
from Autodesk.Revit.DB import PrimarySizeCriterion
from Autodesk.Revit.DB.Plumbing import PipeType, PipeSegment

from pymep_revit import safe_name, ft2mm


SCHEMA_VERSION = "1.0"

# Routing-preference rule groups exported, in the dialog's order. Members
# missing from an older API (resolved with getattr below) are skipped
# with a warning instead of crashing the export.
RULE_GROUP_NAMES = [
    "Segments", "Elbows", "Junctions", "Crosses", "Transitions",
    "Unions", "Flanges", "Caps", "MechanicalJoints",
]


# ---------------------------------------------------------------------------
# pure data shaping (stdlib only - unit-tested without Revit)
# ---------------------------------------------------------------------------
def size_criterion_entry(min_mm, max_mm):
    """PrimarySizeCriterion -> schema dict. The 'matches every size' case
    (``PrimarySizeCriterion.All()`` reads back with a giant sentinel
    maximum) becomes an explicit ``covers_all_sizes`` flag - the JSON
    never carries the magic number. 1e9 mm (1000 km of bore) safely
    separates the sentinel from any real size a user could type."""
    if min_mm is not None and max_mm is not None \
            and min_mm <= 0.0 and max_mm >= 1.0e9:
        return {"type": "size", "covers_all_sizes": True}
    return {"type": "size", "covers_all_sizes": False,
            "min_mm": round(min_mm, 3), "max_mm": round(max_mm, 3)}


def dedupe_fittings(pipe_types):
    """Walk the exported types' rule tables and build the top-level
    ``fittings`` section: one entry per (family, type) with a count of
    how many rules reference it. Identity only - the import side loads
    RFAs and resolves by these names."""
    refs = {}
    for pt in pipe_types:
        for rules in pt.get("routing_preferences", {}).values():
            for rule in rules:
                part = rule.get("part", {})
                if part.get("kind") != "fitting":
                    continue
                key = (part.get("family", ""), part.get("type", ""))
                if key in refs:
                    refs[key]["rule_count"] += 1
                else:
                    refs[key] = {"family": key[0], "type": key[1],
                                 "category": part.get("category", ""),
                                 "rule_count": 1}
    return [refs[k] for k in sorted(refs.keys())]


def build_header(model_title, revit_version, revit_build, exported_iso):
    """The versioned header every export starts with."""
    return {"schema_version": SCHEMA_VERSION,
            "kind": "pymep-pipetypes",
            "source_model": model_title or "",
            "revit_version": revit_version or "",
            "revit_build": revit_build or "",
            "exported": exported_iso,
            "units": "mm"}


def build_payload(header, pipe_types, segments, fittings, warnings):
    """Assemble the full export dict (sections sorted by name so diffs
    between two exports stay readable)."""
    return {"header": header,
            "pipe_types": sorted(pipe_types, key=lambda t: t["name"]),
            "segments": sorted(segments, key=lambda s: s["name"]),
            "fittings": fittings,
            "warnings": list(warnings)}


def summarize_payload(payload):
    """Counts for the output-window summary."""
    rules = 0
    for pt in payload["pipe_types"]:
        for group_rules in pt.get("routing_preferences", {}).values():
            rules += len(group_rules)
    fams = set(f["family"] for f in payload["fittings"])
    return {"types": len(payload["pipe_types"]),
            "rules": rules,
            "segments": len(payload["segments"]),
            "fittings": len(payload["fittings"]),
            "fitting_families": len(fams),
            "warnings": len(payload["warnings"])}


# ---------------------------------------------------------------------------
# Revit API access
# ---------------------------------------------------------------------------
def list_pipe_types(doc):
    """[(name, PipeType), ...] sorted by name."""
    out = []
    for pt in FilteredElementCollector(doc).OfClass(PipeType):
        try:
            out.append((safe_name(pt), pt))
        except Exception:
            continue
    out.sort(key=lambda t: t[0].lower())
    return out


def _bip_string(elem, bip_name):
    bip = getattr(BuiltInParameter, bip_name, None)
    if bip is None:
        return None
    try:
        p = elem.get_Parameter(bip)
        if p is not None and p.HasValue:
            return p.AsString()
    except Exception:
        pass
    return None


def _type_parameters(pt):
    """The writable identity parameters worth round-tripping, only when
    non-empty."""
    out = {}
    for key, bip_name in (("description", "ALL_MODEL_DESCRIPTION"),
                          ("type_comments", "ALL_MODEL_TYPE_COMMENTS"),
                          ("keynote", "KEYNOTE_PARAM")):
        v = _bip_string(pt, bip_name)
        if v:
            out[key] = v
    return out


def _criteria_entries(rule, where, warnings):
    """The rule's criteria as schema dicts. A rule with NO criteria
    applies to every size - exported as the same explicit flag."""
    try:
        n = rule.NumberOfCriteria
    except Exception:
        n = 0
    if not n:
        return [{"type": "size", "covers_all_sizes": True}]
    out = []
    for i in range(n):
        try:
            c = rule.GetCriterion(i)
        except Exception as ex:
            warnings.append("{}: criterion {} unreadable ({})".format(
                where, i, ex))
            continue
        if isinstance(c, PrimarySizeCriterion):
            out.append(size_criterion_entry(ft2mm(c.MinimumSize),
                                            ft2mm(c.MaximumSize)))
        else:
            # only PrimarySizeCriterion exists in the public API today;
            # record anything new rather than losing it silently
            out.append({"type": "unknown",
                        "class": type(c).__name__})
            warnings.append("{}: unrecognised criterion class {} - "
                            "recorded without values".format(
                                where, type(c).__name__))
    return out or [{"type": "size", "covers_all_sizes": True}]


def _mepsize_entry(s):
    return {"nominal_mm": round(ft2mm(s.NominalDiameter), 3),
            "inner_mm": round(ft2mm(s.InnerDiameter), 3),
            "outer_mm": round(ft2mm(s.OuterDiameter), 3),
            "used_in_size_lists": bool(s.UsedInSizeLists),
            "used_in_sizing": bool(s.UsedInSizing)}


def _segment_entry(doc, seg, warnings):
    """PipeSegment -> schema dict: identity by name, roughness, schedule
    and material names, full size catalogue."""
    name = safe_name(seg)
    entry = {"name": name}
    try:
        entry["roughness_mm"] = round(ft2mm(seg.Roughness), 6)
    except Exception:
        warnings.append("segment '{}': roughness unreadable".format(name))
    for key, id_attr in (("schedule_type", "ScheduleTypeId"),
                         ("material", "MaterialId")):
        try:
            eid = getattr(seg, id_attr)
            el = doc.GetElement(eid) if eid is not None else None
            if el is not None:
                entry[key] = safe_name(el)
        except Exception:
            warnings.append("segment '{}': {} unresolvable".format(
                name, key))
    sizes = []
    try:
        for s in seg.GetSizes():
            try:
                sizes.append(_mepsize_entry(s))
            except Exception:
                warnings.append("segment '{}': one size entry "
                                "unreadable".format(name))
    except Exception:
        warnings.append("segment '{}': GetSizes failed".format(name))
    sizes.sort(key=lambda d: d["nominal_mm"])
    entry["sizes"] = sizes
    return entry


def _resolve_part(doc, rule, where, warnings, segments, families):
    """rule.MEPPartId -> schema 'part' dict, collecting segment entries
    and fitting Family objects on the way. Never raises - an invalid or
    unexpected part becomes an 'unresolved' entry plus a warning."""
    try:
        pid = rule.MEPPartId
    except Exception:
        pid = None
    invalid = getattr(ElementId, "InvalidElementId", None)
    if pid is None or (invalid is not None and pid == invalid):
        warnings.append("{}: rule has no part (invalid MEPPartId) - "
                        "exported as unresolved".format(where))
        return {"kind": "unresolved"}
    el = None
    try:
        el = doc.GetElement(pid)
    except Exception:
        pass
    if isinstance(el, PipeSegment):
        seg_name = safe_name(el)
        if seg_name not in segments:
            segments[seg_name] = _segment_entry(doc, el, warnings)
        return {"kind": "segment", "segment": seg_name}
    if isinstance(el, FamilySymbol):
        try:
            fam = el.Family
            fam_name = safe_name(fam)
        except Exception:
            fam, fam_name = None, ""
        cat = ""
        try:
            if el.Category is not None:
                cat = el.Category.Name
        except Exception:
            pass
        if fam is not None and fam_name and fam_name not in families:
            families[fam_name] = fam
        return {"kind": "fitting", "family": fam_name,
                "type": safe_name(el), "category": cat}
    warnings.append("{}: part id resolves to {} - exported as "
                    "unresolved".format(
                        where, type(el).__name__ if el is not None
                        else "nothing"))
    return {"kind": "unresolved"}


def export_pipe_types(doc, pipe_types, log=None):
    """Extract everything for ``pipe_types`` (a list of PipeType).

    Returns ``(payload, families, warnings)``: the JSON-ready payload,
    ``{family_name: Family}`` for the optional RFA save, and the
    warnings list (also embedded in the payload). Never raises for a
    single bad rule - problems become warnings and the export
    continues."""
    warnings = []
    segments = {}
    families = {}
    types_out = []

    for pt in pipe_types:
        name = safe_name(pt)
        entry = {"name": name}
        params = _type_parameters(pt)
        if params:
            entry["parameters"] = params
        try:
            rpm = pt.RoutingPreferenceManager
        except Exception:
            rpm = None
        if rpm is None:
            warnings.append("pipe type '{}': no RoutingPreferenceManager "
                            "- exported name/parameters only".format(name))
            entry["routing_preferences"] = {}
            types_out.append(entry)
            continue
        try:
            entry["preferred_junction_type"] = str(rpm.PreferredJunctionType)
        except Exception:
            warnings.append("pipe type '{}': PreferredJunctionType "
                            "unreadable".format(name))
        groups_out = {}
        for gname in RULE_GROUP_NAMES:
            group = getattr(RoutingPreferenceRuleGroupType, gname, None)
            if group is None:
                # enum member absent in this Revit's API - note it once
                note = ("rule group '{}' not in this Revit's API - "
                        "skipped".format(gname))
                if note not in warnings:
                    warnings.append(note)
                continue
            try:
                n = rpm.GetNumberOfRules(group)
            except Exception:
                n = 0
            rules_out = []
            for i in range(n):
                where = "'{}' > {} > rule {}".format(name, gname, i + 1)
                try:
                    rule = rpm.GetRule(group, i)
                except Exception as ex:
                    warnings.append("{}: unreadable ({})".format(where, ex))
                    continue
                desc = ""
                try:
                    desc = rule.Description or ""
                except Exception:
                    pass
                rules_out.append({
                    "description": desc,
                    "part": _resolve_part(doc, rule, where, warnings,
                                          segments, families),
                    "criteria": _criteria_entries(rule, where, warnings),
                })
            if rules_out:
                groups_out[gname] = rules_out
        entry["routing_preferences"] = groups_out
        types_out.append(entry)
        if log is not None:
            log("  - {}: {} rule(s) across {} group(s)".format(
                name, sum(len(r) for r in groups_out.values()),
                len(groups_out)))

    app = doc.Application
    header = build_header(doc.Title,
                          getattr(app, "VersionNumber", ""),
                          getattr(app, "VersionBuild", ""),
                          datetime.datetime.now().isoformat())
    payload = build_payload(header, types_out,
                            list(segments.values()),
                            dedupe_fittings(types_out), warnings)
    return payload, families, warnings


def write_export(path, payload):
    """Write the payload as pretty-printed JSON (ascii-escaped, so any
    text editor/encoding on the import side is safe)."""
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, sort_keys=True)


def save_fitting_rfas(doc, families, folder, log=None):
    """Save each referenced fitting family as ``<folder>/<name>.rfa`` via
    ``doc.EditFamily``. Returns ``(saved_names, failed_pairs)``.

    NOTE for callers: these RFAs are written in the CURRENT model's Revit
    version - Revit cannot open them in an OLDER version, so they are for
    reference or same-version/upward reuse only. Print that warning."""
    if not os.path.isdir(folder):
        os.makedirs(folder)
    saved = []
    failed = []
    for name in sorted(families.keys()):
        fam = families[name]
        try:
            if not fam.IsEditable:
                raise Exception("family is not editable (system or "
                                "in-place)")
            fdoc = doc.EditFamily(fam)
            try:
                opts = SaveAsOptions()
                opts.OverwriteExistingFile = True
                fdoc.SaveAs(os.path.join(folder, name + ".rfa"), opts)
            finally:
                fdoc.Close(False)
            saved.append(name)
            if log is not None:
                log("  + saved {}.rfa".format(name))
        except Exception as ex:
            failed.append((name, str(ex)))
            if log is not None:
                log("  ! {}: NOT saved - {}".format(name, ex))
    return saved, failed
