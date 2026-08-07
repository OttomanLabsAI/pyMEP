# -*- coding: utf-8 -*-
"""View template / filter transfer - the EXPORT side.

Turns ParameterFilterElements and view templates of the active model
into the version-agnostic JSON of pymep_vt_schema. Fail-soft: every
item serializes inside its own try/except and lands in the results
list as exported / skipped / degraded with a reason - one broken item
never aborts the run. Selection filters are out of scope (their
element ids mean nothing in another model) and are reported skipped.

IronPython 2.7 / Revit 2022-2026 (API drift handled in
pymep_vt_compat).
"""

import clr
clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import (
    BuiltInParameter, Category, ElementParameterFilter,
    FilterDoubleRule, FilterElementIdRule, FilterIntegerRule,
    FilterInverseRule, FilterStringRule, FilteredElementCollector,
    HasNoValueFilterRule, HasValueFilterRule, LogicalAndFilter,
    LogicalOrFilter, OverrideGraphicSettings, ParameterElement,
    ParameterFilterElement, PlanViewPlane, SelectionFilterElement,
    SharedParameterElement, ViewPlan,
)

from pymep_vt_compat import (
    bic_name, bip_name, fill_pattern_name, id_value,
    line_pattern_name, revit_version,
)
from pymep_vt_schema import (
    NUMERIC_EVALUATORS, STRING_EVALUATORS, make_document,
)

_VIEW_RANGE_PLANES = (
    ("top", "TopClipPlane"),
    ("cut", "CutPlane"),
    ("bottom", "BottomClipPlane"),
    ("view_depth", "ViewDepthPlane"),
)


# ---------------------------------------------------------------------------
# parameters + rules
# ---------------------------------------------------------------------------
def _param_identity(doc, param_id):
    """{'kind': 'builtin'|'shared'|'project', ...} or raises with the
    reason (the whole filter is skipped - never silently altered)."""
    v = id_value(param_id)
    if v < 0:
        name = bip_name(v)
        if not name:
            raise ValueError("built-in parameter id {} has no enum "
                             "name in this Revit".format(v))
        return {"kind": "builtin", "id": name}
    el = doc.GetElement(param_id)
    if isinstance(el, SharedParameterElement):
        return {"kind": "shared", "guid": str(el.GuidValue),
                "name": el.Name}
    if isinstance(el, ParameterElement):
        return {"kind": "project", "name": el.Name}
    raise ValueError("parameter id {} is neither built-in, shared nor "
                     "project".format(v))


def _element_ref(doc, eid):
    """Cross-model reference for an ElementId rule value: category
    enum name + element name."""
    el = doc.GetElement(eid)
    if el is None:
        raise ValueError("id rule points at element {} which no longer "
                         "exists".format(id_value(eid)))
    cat = el.Category
    cname = bic_name(cat.Id) if cat is not None else None
    try:
        name = el.Name
    except Exception:
        name = None
    if not name:
        raise ValueError("id rule target {} has no name".format(
            id_value(eid)))
    return {"category": cname, "name": name}


def _serialize_rule(doc, rule):
    inverted = False
    if isinstance(rule, FilterInverseRule):
        inverted = True
        rule = rule.GetInnerRule()
    if isinstance(rule, HasValueFilterRule):
        return {"parameter": _param_identity(doc, rule.GetRuleParameter()),
                "rule": "has_value", "inverted": inverted}
    if isinstance(rule, HasNoValueFilterRule):
        return {"parameter": _param_identity(doc, rule.GetRuleParameter()),
                "rule": "has_no_value", "inverted": inverted}
    if isinstance(rule, FilterStringRule):
        ev = STRING_EVALUATORS.get(rule.GetEvaluator().GetType().Name)
        if ev is None:
            raise ValueError("unknown string evaluator {}".format(
                rule.GetEvaluator().GetType().Name))
        return {"parameter": _param_identity(doc, rule.GetRuleParameter()),
                "rule": "string", "evaluator": ev,
                "value": rule.RuleString, "inverted": inverted}
    if isinstance(rule, FilterDoubleRule):
        ev = NUMERIC_EVALUATORS.get(rule.GetEvaluator().GetType().Name)
        if ev is None:
            raise ValueError("unknown double evaluator {}".format(
                rule.GetEvaluator().GetType().Name))
        return {"parameter": _param_identity(doc, rule.GetRuleParameter()),
                "rule": "double", "evaluator": ev,
                "value": rule.RuleValue, "epsilon": rule.Epsilon,
                "inverted": inverted}
    if isinstance(rule, FilterIntegerRule):
        ev = NUMERIC_EVALUATORS.get(rule.GetEvaluator().GetType().Name)
        if ev is None:
            raise ValueError("unknown integer evaluator {}".format(
                rule.GetEvaluator().GetType().Name))
        return {"parameter": _param_identity(doc, rule.GetRuleParameter()),
                "rule": "integer", "evaluator": ev,
                "value": rule.RuleValue, "inverted": inverted}
    if isinstance(rule, FilterElementIdRule):
        ev = NUMERIC_EVALUATORS.get(rule.GetEvaluator().GetType().Name)
        if ev is None:
            raise ValueError("unknown id evaluator {}".format(
                rule.GetEvaluator().GetType().Name))
        return {"parameter": _param_identity(doc, rule.GetRuleParameter()),
                "rule": "element_id", "evaluator": ev,
                "value": _element_ref(doc, rule.RuleValue),
                "inverted": inverted}
    raise ValueError("unsupported rule type {}".format(
        rule.GetType().Name))


def _walk_element_filter(doc, ef):
    if isinstance(ef, LogicalAndFilter) or isinstance(ef, LogicalOrFilter):
        logic = "and" if isinstance(ef, LogicalAndFilter) else "or"
        children = [_walk_element_filter(doc, c) for c in ef.GetFilters()]
        return {"logic": logic, "children": children}
    if isinstance(ef, ElementParameterFilter):
        return {"logic": "rules",
                "rules": [_serialize_rule(doc, r) for r in ef.GetRules()]}
    raise ValueError("unsupported nested filter type {}".format(
        ef.GetType().Name))


def serialize_filter(doc, pfe):
    """(filter_dict, None) or (None, reason). Any unresolvable piece
    fails the WHOLE filter - never exported with altered logic."""
    try:
        cats = []
        for cid in pfe.GetCategories():
            nm = bic_name(cid)
            if nm is None:
                return None, "category id {} is not a built-in " \
                    "category".format(id_value(cid))
            cats.append(nm)
        out = {"name": pfe.Name, "categories": sorted(cats),
               "element_filter": None}
        ef = pfe.GetElementFilter()
        if ef is not None:
            out["element_filter"] = _walk_element_filter(doc, ef)
        return out, None
    except Exception as ex:
        return None, str(ex)


# ---------------------------------------------------------------------------
# override graphic settings
# ---------------------------------------------------------------------------
def _color_list(color):
    try:
        if color is not None and color.IsValid:
            return [color.Red, color.Green, color.Blue]
    except Exception:
        pass
    return None


def serialize_ogs(doc, ogs):
    """OverrideGraphicSettings -> dict; unset pieces stay out (weight
    -1 / invalid color / invalid pattern id are 'not set')."""
    out = {}
    try:
        if ogs.Halftone:
            out["halftone"] = True
    except Exception:
        pass
    try:
        if ogs.Transparency:
            out["transparency"] = ogs.Transparency
    except Exception:
        pass
    try:
        dl = str(ogs.DetailLevel)
        if dl != "Undefined":
            out["detail_level"] = dl
    except Exception:
        pass

    def lines(weight, color, pattern_id):
        d = {}
        if weight != -1:
            d["weight"] = weight
        c = _color_list(color)
        if c is not None:
            d["color"] = c
        p = line_pattern_name(doc, pattern_id)
        if p is not None:
            d["pattern"] = p
        return d

    def fills(pattern_id, color, visible):
        d = {}
        p = fill_pattern_name(doc, pattern_id)
        if p is not None:
            d["pattern"] = p
        c = _color_list(color)
        if c is not None:
            d["color"] = c
        if visible is not None and not visible:
            d["visible"] = False
        return d

    try:
        d = lines(ogs.ProjectionLineWeight, ogs.ProjectionLineColor,
                  ogs.ProjectionLinePatternId)
        if d:
            out["projection_lines"] = d
    except Exception:
        pass
    try:
        d = lines(ogs.CutLineWeight, ogs.CutLineColor,
                  ogs.CutLinePatternId)
        if d:
            out["cut_lines"] = d
    except Exception:
        pass
    try:
        d = fills(ogs.SurfaceForegroundPatternId,
                  ogs.SurfaceForegroundPatternColor,
                  ogs.IsSurfaceForegroundPatternVisible)
        if d:
            out["surface_fg"] = d
    except Exception:
        pass
    try:
        d = fills(ogs.SurfaceBackgroundPatternId,
                  ogs.SurfaceBackgroundPatternColor,
                  ogs.IsSurfaceBackgroundPatternVisible)
        if d:
            out["surface_bg"] = d
    except Exception:
        pass
    try:
        d = fills(ogs.CutForegroundPatternId,
                  ogs.CutForegroundPatternColor,
                  ogs.IsCutForegroundPatternVisible)
        if d:
            out["cut_fg"] = d
    except Exception:
        pass
    try:
        d = fills(ogs.CutBackgroundPatternId,
                  ogs.CutBackgroundPatternColor,
                  ogs.IsCutBackgroundPatternVisible)
        if d:
            out["cut_bg"] = d
    except Exception:
        pass
    return out


# ---------------------------------------------------------------------------
# view templates
# ---------------------------------------------------------------------------
def _builtin_categories(doc):
    """[(Category, bic_name)] for the model's built-in categories."""
    out = []
    try:
        for cat in doc.Settings.Categories:
            nm = bic_name(cat.Id)
            if nm:
                out.append((cat, nm))
    except Exception:
        pass
    return out


def serialize_template(doc, view, notes):
    """One view template -> dict; per-piece failures land in notes
    (the template still exports with what worked)."""
    t = {"name": view.Name, "view_family": str(view.ViewType),
         "properties": {}, "uncontrolled_params": [],
         "category_visibility": [], "category_overrides": [],
         "filters": []}

    props = t["properties"]
    for key, fn in (
            ("scale", lambda: view.Scale),
            ("detail_level", lambda: str(view.DetailLevel)),
            ("discipline", lambda: str(view.Discipline)),
            ("parts_visibility", lambda: str(view.PartsVisibility)),
            ("display_style", lambda: str(view.DisplayStyle))):
        try:
            props[key] = fn()
        except Exception:
            pass

    try:
        for pid in view.GetNonControlledTemplateParameterIds():
            nm = bip_name(pid)
            if nm:
                t["uncontrolled_params"].append(nm)
            else:
                notes.append("{}: uncontrolled project-param id {} "
                             "cannot cross models - dropped".format(
                                 view.Name, id_value(pid)))
        t["uncontrolled_params"].sort()
    except Exception:
        pass

    default_ogs = serialize_ogs(doc, OverrideGraphicSettings())
    for cat, nm in _builtin_categories(doc):
        try:
            if view.GetCategoryHidden(cat.Id):
                t["category_visibility"].append(
                    {"category": nm, "subcategory": None, "hidden": True})
        except Exception:
            pass
        try:
            ogs = serialize_ogs(doc, view.GetCategoryOverrides(cat.Id))
            if ogs and ogs != default_ogs:
                t["category_overrides"].append(
                    {"category": nm, "subcategory": None,
                     "overrides": ogs})
        except Exception:
            pass
        try:
            for sub in cat.SubCategories:
                try:
                    if view.GetCategoryHidden(sub.Id):
                        t["category_visibility"].append(
                            {"category": nm, "subcategory": sub.Name,
                             "hidden": True})
                except Exception:
                    pass
                try:
                    ogs = serialize_ogs(
                        doc, view.GetCategoryOverrides(sub.Id))
                    if ogs and ogs != default_ogs:
                        t["category_overrides"].append(
                            {"category": nm, "subcategory": sub.Name,
                             "overrides": ogs})
                except Exception:
                    pass
        except Exception:
            pass
    t["category_visibility"].sort(
        key=lambda d: (d["category"], d["subcategory"] or ""))
    t["category_overrides"].sort(
        key=lambda d: (d["category"], d["subcategory"] or ""))

    try:
        for fid in view.GetFilters():
            fel = doc.GetElement(fid)
            if isinstance(fel, SelectionFilterElement):
                notes.append("{}: selection filter '{}' cannot cross "
                             "models - dropped".format(
                                 view.Name, fel.Name))
                continue
            row = {"name": fel.Name}
            try:
                row["enabled"] = view.GetIsFilterEnabled(fid)
            except Exception:
                pass
            try:
                row["visible"] = view.GetFilterVisibility(fid)
            except Exception:
                pass
            try:
                row["overrides"] = serialize_ogs(
                    doc, view.GetFilterOverrides(fid))
            except Exception:
                row["overrides"] = {}
            t["filters"].append(row)
        t["filters"].sort(key=lambda d: d["name"])
    except Exception:
        pass

    if isinstance(view, ViewPlan):
        try:
            vr = view.GetViewRange()
            ranges = {}
            for key, plane_name in _VIEW_RANGE_PLANES:
                plane = getattr(PlanViewPlane, plane_name)
                lid = vr.GetLevelId(plane)
                v = id_value(lid)
                entry = {"offset": vr.GetOffset(plane)}
                if v < 0:
                    entry["special"] = v
                else:
                    lvl = doc.GetElement(lid)
                    entry["level"] = lvl.Name if lvl is not None else None
                ranges[key] = entry
            t["view_range"] = ranges
        except Exception:
            pass

    try:
        p = view.get_Parameter(BuiltInParameter.VIEW_PHASE_FILTER)
        if p is not None and p.HasValue:
            pf = doc.GetElement(p.AsElementId())
            if pf is not None:
                t["phase_filter"] = pf.Name
    except Exception:
        pass

    return t


# ---------------------------------------------------------------------------
# whole document
# ---------------------------------------------------------------------------
def referenced_filter_ids(doc, views):
    """Rule-based filter ids the given templates reference."""
    seen = []
    for view in views:
        try:
            for fid in view.GetFilters():
                if isinstance(doc.GetElement(fid),
                              ParameterFilterElement) and fid not in seen:
                    seen.append(fid)
        except Exception:
            continue
    return seen


def export_document(doc, template_views, extra_filter_elements):
    """(data, results): the JSON-ready dict for the picked templates
    (their referenced filters auto-included) plus any standalone
    filters. results rows: {'item', 'kind', 'status', 'reason'}."""
    results = []
    ver, build = revit_version(doc)
    data = make_document(ver, build)

    filter_els = []
    seen_ids = set()
    for fid in referenced_filter_ids(doc, template_views):
        filter_els.append(doc.GetElement(fid))
        seen_ids.add(id_value(fid))
    for fel in extra_filter_elements or []:
        if id_value(fel.Id) not in seen_ids:
            filter_els.append(fel)
            seen_ids.add(id_value(fel.Id))

    for fel in filter_els:
        if isinstance(fel, SelectionFilterElement):
            results.append({"item": fel.Name, "kind": "filter",
                            "status": "skipped",
                            "reason": "selection filter - element ids "
                                      "mean nothing in another model"})
            continue
        d, reason = serialize_filter(doc, fel)
        if d is None:
            results.append({"item": fel.Name, "kind": "filter",
                            "status": "skipped", "reason": reason})
        else:
            data["filters"].append(d)
            results.append({"item": fel.Name, "kind": "filter",
                            "status": "exported", "reason": ""})
    data["filters"].sort(key=lambda d: d["name"])

    for view in template_views:
        notes = []
        try:
            t = serialize_template(doc, view, notes)
            data["view_templates"].append(t)
            results.append({"item": view.Name, "kind": "template",
                            "status": "degraded" if notes else "exported",
                            "reason": "; ".join(notes)})
        except Exception as ex:
            results.append({"item": view.Name, "kind": "template",
                            "status": "skipped", "reason": str(ex)})
    data["view_templates"].sort(key=lambda d: d["name"])
    return data, results
