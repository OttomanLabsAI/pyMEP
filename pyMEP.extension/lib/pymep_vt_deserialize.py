# -*- coding: utf-8 -*-
"""View template / filter transfer - the IMPORT side.

Rebuilds ParameterFilterElements and view templates from the JSON of
pymep_vt_schema. Filters first, then templates. Same-name items are
updated IN PLACE (SetCategories / SetElementFilter, template state
re-applied) so existing view assignments keep working - never
delete-and-recreate. Fail-soft: every item imports inside its own
try/except and lands in results as created / updated / skipped /
degraded / failed with a reason.

IronPython 2.7 / Revit 2022-2026 (API drift handled in
pymep_vt_compat).
"""

import clr
clr.AddReference("RevitAPI")

import System
from System.Collections.Generic import List

from Autodesk.Revit.DB import (
    BuiltInParameter, Color, ElementFilter, ElementId,
    ElementParameterFilter, FillGrid, FillPattern, FillPatternElement,
    FillPatternHostOrientation, FillPatternTarget, FilterInverseRule,
    FilterRule, FilteredElementCollector, Level, LinePattern,
    LinePatternElement, LinePatternSegment, LinePatternSegmentType,
    LogicalAndFilter, LogicalOrFilter, OverrideGraphicSettings,
    ParameterElement, ParameterFilterElement, PhaseFilter,
    PlanViewPlane, SharedParameterElement, UV, View, ViewDrafting,
    ViewFamily, ViewFamilyType, ViewPlan, View3D,
)

import Autodesk.Revit.DB as DB

from pymep_vt_compat import (
    bic_id, bic_name, bip_id, create_double_rule,
    create_element_id_rule, create_has_value_rule,
    create_integer_rule, create_string_rule, fill_pattern_by_name,
    id_value, line_pattern_by_name, make_id,
)
from pymep_vt_schema import family_label

_VIEW_RANGE_PLANES = {
    "top": "TopClipPlane",
    "cut": "CutPlane",
    "bottom": "BottomClipPlane",
    "view_depth": "ViewDepthPlane",
}

# view_family (str(ViewType)) -> the ViewFamily whose type creates it
_PLAN_FAMILIES = {
    "FloorPlan": "FloorPlan",
    "CeilingPlan": "CeilingPlan",
    "EngineeringPlan": "StructuralPlan",
}


# ---------------------------------------------------------------------------
# parameter + element resolution
# ---------------------------------------------------------------------------
def resolve_parameter(doc, pdict):
    """ElementId for a schema parameter identity, or raises with the
    reason. Order: built-in enum name; shared by GUID then name;
    project by name."""
    kind = pdict.get("kind")
    if kind == "builtin":
        v = bip_id(pdict.get("id"))
        if v is None:
            raise ValueError("this Revit has no built-in parameter "
                             "{}".format(pdict.get("id")))
        return make_id(v)
    if kind == "shared":
        want_guid = (pdict.get("guid") or "").lower()
        by_name = None
        for sp in FilteredElementCollector(doc).OfClass(
                SharedParameterElement):
            try:
                if str(sp.GuidValue).lower() == want_guid:
                    return sp.Id
                if sp.Name == pdict.get("name") and by_name is None:
                    by_name = sp.Id
            except Exception:
                continue
        if by_name is not None:
            return by_name
        raise ValueError("shared parameter '{}' ({}) not in this "
                         "model".format(pdict.get("name"),
                                        pdict.get("guid")))
    if kind == "project":
        for pe in FilteredElementCollector(doc).OfClass(ParameterElement):
            try:
                if isinstance(pe, SharedParameterElement):
                    continue
                if pe.Name == pdict.get("name"):
                    return pe.Id
            except Exception:
                continue
        raise ValueError("project parameter '{}' not in this "
                         "model".format(pdict.get("name")))
    raise ValueError("unknown parameter kind '{}'".format(kind))


def resolve_element_ref(doc, ref):
    """ElementId for a {'category', 'name'} reference (levels, types,
    ...), instances first then types. Raises when nothing matches."""
    bic = bic_id(ref.get("category")) if ref.get("category") else None
    name = ref.get("name")

    def _scan(collector):
        for el in collector:
            try:
                if el.Name == name:
                    return el.Id
            except Exception:
                continue
        return None

    if bic is not None:
        cat_id = make_id(bic)
        for types_only in (False, True):
            col = FilteredElementCollector(doc).OfCategoryId(cat_id)
            col = col.WhereElementIsElementType() if types_only \
                else col.WhereElementIsNotElementType()
            found = _scan(col)
            if found is not None:
                return found
    else:
        found = _scan(FilteredElementCollector(doc)
                      .WhereElementIsNotElementType())
        if found is not None:
            return found
    raise ValueError("no element named '{}' in category {}".format(
        name, ref.get("category") or "(any)"))


# ---------------------------------------------------------------------------
# rules + element filters
# ---------------------------------------------------------------------------
def build_rule(doc, rdict):
    pid = resolve_parameter(doc, rdict.get("parameter") or {})
    kind = rdict.get("rule")
    if kind == "has_value":
        rule = create_has_value_rule(pid, True)
    elif kind == "has_no_value":
        rule = create_has_value_rule(pid, False)
    elif kind == "string":
        rule = create_string_rule(pid, rdict.get("evaluator"),
                                  rdict.get("value") or "")
    elif kind == "double":
        rule = create_double_rule(pid, rdict.get("evaluator"),
                                  rdict.get("value") or 0.0,
                                  rdict.get("epsilon") or 1e-6)
    elif kind == "integer":
        rule = create_integer_rule(pid, rdict.get("evaluator"),
                                   rdict.get("value") or 0)
    elif kind == "element_id":
        target = resolve_element_ref(doc, rdict.get("value") or {})
        rule = create_element_id_rule(pid, rdict.get("evaluator"),
                                      target)
    else:
        raise ValueError("unknown rule kind '{}'".format(kind))
    if rdict.get("inverted"):
        rule = FilterInverseRule(rule)
    return rule


def build_element_filter(doc, node):
    logic = node.get("logic")
    if logic == "rules":
        lst = List[FilterRule]()
        for r in node.get("rules", []):
            lst.Add(build_rule(doc, r))
        return ElementParameterFilter(lst)
    if logic in ("and", "or"):
        kids = List[ElementFilter]()
        for c in node.get("children", []):
            kids.Add(build_element_filter(doc, c))
        return LogicalAndFilter(kids) if logic == "and" \
            else LogicalOrFilter(kids)
    raise ValueError("unknown logic '{}'".format(logic))


def _category_ids(fdict):
    ids = List[ElementId]()
    for nm in fdict.get("categories", []):
        v = bic_id(nm)
        if v is None:
            raise ValueError("this Revit has no category {}".format(nm))
        ids.Add(make_id(v))
    return ids


def import_filter(doc, fdict, update_existing=True):
    """{'item', 'kind', 'status', 'reason'} - creates or updates ONE
    ParameterFilterElement. Unresolvable rules skip the whole filter,
    never alter its logic."""
    name = fdict.get("name") or "(unnamed)"
    row = {"item": name, "kind": "filter", "status": "failed",
           "reason": ""}
    try:
        existing = None
        for pfe in FilteredElementCollector(doc).OfClass(
                ParameterFilterElement):
            if pfe.Name == name:
                existing = pfe
                break
        cats = _category_ids(fdict)
        ef = None
        if fdict.get("element_filter"):
            ef = build_element_filter(doc, fdict["element_filter"])
        if existing is not None:
            if not update_existing:
                row["status"] = "skipped"
                row["reason"] = "already exists (skip existing chosen)"
                return row
            existing.SetCategories(cats)
            if ef is not None:
                existing.SetElementFilter(ef)
            row["status"] = "updated"
            return row
        if ef is not None:
            ParameterFilterElement.Create(doc, name, cats, ef)
        else:
            ParameterFilterElement.Create(doc, name, cats)
        row["status"] = "created"
        return row
    except Exception as ex:
        row["status"] = "skipped"
        row["reason"] = str(ex)
        return row


# ---------------------------------------------------------------------------
# override graphic settings
# ---------------------------------------------------------------------------
def build_ogs(doc, d, notes, where):
    """dict -> OverrideGraphicSettings, applying ONLY set values.
    Missing patterns drop that one piece with a note (degraded)."""
    ogs = OverrideGraphicSettings()
    if not d:
        return ogs
    if d.get("halftone"):
        ogs.SetHalftone(True)
    if d.get("transparency"):
        ogs.SetSurfaceTransparency(int(d["transparency"]))
    if d.get("detail_level"):
        try:
            from Autodesk.Revit.DB import ViewDetailLevel
            ogs.SetDetailLevel(getattr(ViewDetailLevel,
                                       d["detail_level"]))
        except Exception:
            notes.append("{}: unknown detail level {}".format(
                where, d["detail_level"]))

    def color_of(lst):
        return Color(System.Byte(lst[0]), System.Byte(lst[1]),
                     System.Byte(lst[2]))

    def lines(sub, set_weight, set_color, set_pattern):
        if not sub:
            return
        if sub.get("weight") is not None:
            set_weight(int(sub["weight"]))
        if sub.get("color"):
            set_color(color_of(sub["color"]))
        if sub.get("pattern"):
            pid = line_pattern_by_name(doc, sub["pattern"])
            if pid is None:
                notes.append("{}: line pattern '{}' not in this "
                             "model".format(where, sub["pattern"]))
            else:
                set_pattern(pid)

    def fills(sub, set_pattern, set_color, set_visible):
        if not sub:
            return
        if sub.get("pattern"):
            pid = fill_pattern_by_name(doc, sub["pattern"])
            if pid is None:
                notes.append("{}: fill pattern '{}' not in this "
                             "model".format(where, sub["pattern"]))
            else:
                set_pattern(pid)
        if sub.get("color"):
            set_color(color_of(sub["color"]))
        if sub.get("visible") is False:
            set_visible(False)

    lines(d.get("projection_lines"), ogs.SetProjectionLineWeight,
          ogs.SetProjectionLineColor, ogs.SetProjectionLinePatternId)
    lines(d.get("cut_lines"), ogs.SetCutLineWeight,
          ogs.SetCutLineColor, ogs.SetCutLinePatternId)
    fills(d.get("surface_fg"), ogs.SetSurfaceForegroundPatternId,
          ogs.SetSurfaceForegroundPatternColor,
          ogs.SetSurfaceForegroundPatternVisible)
    fills(d.get("surface_bg"), ogs.SetSurfaceBackgroundPatternId,
          ogs.SetSurfaceBackgroundPatternColor,
          ogs.SetSurfaceBackgroundPatternVisible)
    fills(d.get("cut_fg"), ogs.SetCutForegroundPatternId,
          ogs.SetCutForegroundPatternColor,
          ogs.SetCutForegroundPatternVisible)
    fills(d.get("cut_bg"), ogs.SetCutBackgroundPatternId,
          ogs.SetCutBackgroundPatternColor,
          ogs.SetCutBackgroundPatternVisible)
    return ogs


# ---------------------------------------------------------------------------
# view template creation
# ---------------------------------------------------------------------------
def _view_family_type(doc, family_name):
    fam = getattr(ViewFamily, family_name, None)
    if fam is None:
        return None
    for vft in FilteredElementCollector(doc).OfClass(ViewFamilyType):
        try:
            if vft.ViewFamily == fam:
                return vft
        except Exception:
            continue
    return None


def _first_level(doc):
    for lvl in FilteredElementCollector(doc).OfClass(Level):
        return lvl
    return None


def _make_template_for_family(doc, view_family, name):
    """(template_view, temp_view_id_or_None) or raises. Plans / 3D /
    drafting come from a throwaway view; sections & elevations borrow
    an existing view of the family as donor (they cannot be created
    without host geometry)."""
    temp = None
    if view_family in _PLAN_FAMILIES:
        vft = _view_family_type(doc, _PLAN_FAMILIES[view_family])
        lvl = _first_level(doc)
        if vft is None or lvl is None:
            raise ValueError("no {} view type / level to create "
                             "from".format(view_family))
        temp = ViewPlan.Create(doc, vft.Id, lvl.Id)
    elif view_family == "ThreeD":
        vft = _view_family_type(doc, "ThreeDimensional")
        if vft is None:
            raise ValueError("no 3D view type in this model")
        temp = View3D.CreateIsometric(doc, vft.Id)
    elif view_family == "DraftingView":
        vft = _view_family_type(doc, "Drafting")
        if vft is None:
            raise ValueError("no drafting view type in this model")
        temp = ViewDrafting.Create(doc, vft.Id)
    else:
        # sections / elevations / details: borrow an existing view
        donor = None
        for v in FilteredElementCollector(doc).OfClass(View):
            try:
                if not v.IsTemplate and str(v.ViewType) == view_family \
                        and v.CanUseTemporaryVisibilityModes():
                    donor = v
                    break
            except Exception:
                continue
        if donor is None:
            raise ValueError(
                "no existing {} view to create the template from - "
                "make one and re-run".format(view_family))
        tmpl = donor.CreateViewTemplate()
        tmpl.Name = name
        return tmpl, None
    tmpl = temp.CreateViewTemplate()
    tmpl.Name = name
    return tmpl, temp.Id


def _apply_template_state(doc, view, tdict, filter_ids_by_name, notes):
    """Write the FULL serialized state onto the template - creation
    inherits the donor view's state, so nothing is assumed."""
    name = tdict.get("name")
    props = tdict.get("properties") or {}
    if props.get("scale"):
        try:
            view.Scale = int(props["scale"])
        except Exception:
            notes.append("scale not applied")
    for key, setter in (("detail_level", "DetailLevel"),
                        ("discipline", "Discipline"),
                        ("parts_visibility", "PartsVisibility"),
                        ("display_style", "DisplayStyle")):
        val = props.get(key)
        if not val:
            continue
        try:
            enum_type = {"DetailLevel": "ViewDetailLevel",
                         "Discipline": "ViewDiscipline",
                         "PartsVisibility": "PartsVisibility",
                         "DisplayStyle": "DisplayStyle"}[setter]
            setattr(view, setter,
                    getattr(getattr(DB, enum_type), val))
        except Exception:
            notes.append("{} '{}' not applied".format(key, val))

    # category visibility: full pass - serialized entries hide, every
    # other built-in (sub)category shows, so donor state never bleeds
    hidden = {}
    for row in tdict.get("category_visibility", []):
        hidden[(row.get("category"), row.get("subcategory"))] = True
    over = {}
    for row in tdict.get("category_overrides", []):
        over[(row.get("category"), row.get("subcategory"))] = \
            row.get("overrides") or {}
    default_ogs = OverrideGraphicSettings()
    try:
        cats = list(doc.Settings.Categories)
    except Exception:
        cats = []
    for cat in cats:
        nm = bic_name(cat.Id)
        if not nm:
            continue
        targets = [(cat.Id, (nm, None))]
        try:
            for sub in cat.SubCategories:
                targets.append((sub.Id, (nm, sub.Name)))
        except Exception:
            pass
        for cid, key in targets:
            try:
                view.SetCategoryHidden(cid, bool(hidden.get(key)))
            except Exception:
                pass
            try:
                if key in over:
                    view.SetCategoryOverrides(
                        cid, build_ogs(doc, over[key], notes,
                                       "{} {}".format(name, key)))
                else:
                    view.SetCategoryOverrides(cid, default_ogs)
            except Exception:
                pass
    # serialized custom subcategories that never matched a target
    known = set()
    for cat in cats:
        nm = bic_name(cat.Id)
        if not nm:
            continue
        known.add((nm, None))
        try:
            for sub in cat.SubCategories:
                known.add((nm, sub.Name))
        except Exception:
            pass
    for key in list(hidden.keys()) + list(over.keys()):
        if key not in known:
            notes.append("subcategory {}/{} not in this model - "
                         "entry skipped".format(key[0], key[1]))

    # filters: serialized set becomes THE set - extras come off
    want = {}
    for row in tdict.get("filters", []):
        want[row.get("name")] = row
    try:
        for fid in list(view.GetFilters()):
            fel = doc.GetElement(fid)
            fname = fel.Name if fel is not None else None
            if fname not in want:
                try:
                    view.RemoveFilter(fid)
                except Exception:
                    pass
    except Exception:
        pass
    for fname, row in sorted(want.items()):
        fid = filter_ids_by_name.get(fname)
        if fid is None:
            notes.append("filter '{}' not in the model (import it "
                         "too) - not attached".format(fname))
            continue
        try:
            if fid not in view.GetFilters():
                view.AddFilter(fid)
            view.SetFilterOverrides(
                fid, build_ogs(doc, row.get("overrides") or {}, notes,
                               "{} filter {}".format(name, fname)))
            if row.get("visible") is not None:
                view.SetFilterVisibility(fid, bool(row["visible"]))
            if row.get("enabled") is not None:
                try:
                    view.SetIsFilterEnabled(fid, bool(row["enabled"]))
                except Exception:
                    pass
        except Exception as ex:
            notes.append("filter '{}' not applied: {}".format(
                fname, ex))

    vr_data = tdict.get("view_range")
    if vr_data and isinstance(view, ViewPlan):
        try:
            vr = view.GetViewRange()
            for key, entry in vr_data.items():
                plane_name = _VIEW_RANGE_PLANES.get(key)
                if plane_name is None:
                    continue
                plane = getattr(PlanViewPlane, plane_name)
                if entry.get("special") is not None:
                    vr.SetLevelId(plane, make_id(entry["special"]))
                elif entry.get("level"):
                    lid = None
                    for lvl in FilteredElementCollector(doc).OfClass(
                            Level):
                        if lvl.Name == entry["level"]:
                            lid = lvl.Id
                            break
                    if lid is None:
                        notes.append("view range: no level '{}' - "
                                     "plane kept as is".format(
                                         entry["level"]))
                        continue
                    vr.SetLevelId(plane, lid)
                if entry.get("offset") is not None:
                    vr.SetOffset(plane, float(entry["offset"]))
            view.SetViewRange(vr)
        except Exception as ex:
            notes.append("view range not applied: {}".format(ex))

    if tdict.get("phase_filter"):
        try:
            target = None
            for pf in FilteredElementCollector(doc).OfClass(PhaseFilter):
                if pf.Name == tdict["phase_filter"]:
                    target = pf.Id
                    break
            if target is None:
                notes.append("phase filter '{}' not in this "
                             "model".format(tdict["phase_filter"]))
            else:
                p = view.get_Parameter(
                    BuiltInParameter.VIEW_PHASE_FILTER)
                if p is not None and not p.IsReadOnly:
                    p.Set(target)
        except Exception:
            notes.append("phase filter not applied")

    # the uncontrolled-parameter list goes LAST - it decides what the
    # template controls from here on
    try:
        ids = List[ElementId]()
        for nm in tdict.get("uncontrolled_params", []):
            v = bip_id(nm)
            if v is None:
                notes.append("uncontrolled param {} unknown "
                             "here".format(nm))
                continue
            ids.Add(make_id(v))
        view.SetNonControlledTemplateParameterIds(ids)
    except Exception:
        pass


def import_template(doc, tdict, filter_ids_by_name,
                    update_existing=True):
    """{'item', 'kind', 'status', 'reason'} - creates or updates ONE
    view template and applies the full serialized state."""
    name = tdict.get("name") or "(unnamed)"
    family = tdict.get("view_family") or ""
    row = {"item": name, "kind": "template", "status": "failed",
           "reason": ""}
    notes = []
    try:
        existing = None
        for v in FilteredElementCollector(doc).OfClass(View):
            try:
                if v.IsTemplate and v.Name == name:
                    existing = v
                    break
            except Exception:
                continue
        if existing is not None:
            if str(existing.ViewType) != family:
                row["status"] = "skipped"
                row["reason"] = "a '{}' template of this name exists " \
                    "(file wants {})".format(
                        family_label(str(existing.ViewType)),
                        family_label(family))
                return row
            if not update_existing:
                row["status"] = "skipped"
                row["reason"] = "already exists (skip existing chosen)"
                return row
            _apply_template_state(doc, existing, tdict,
                                  filter_ids_by_name, notes)
            row["status"] = "degraded" if notes else "updated"
            row["reason"] = "; ".join(notes)
            return row

        tmpl, temp_id = _make_template_for_family(doc, family, name)
        try:
            _apply_template_state(doc, tmpl, tdict,
                                  filter_ids_by_name, notes)
        finally:
            if temp_id is not None:
                try:
                    doc.Delete(temp_id)
                except Exception:
                    pass
        row["status"] = "degraded" if notes else "created"
        row["reason"] = "; ".join(notes)
        return row
    except Exception as ex:
        row["reason"] = str(ex)
        return row


def import_level(doc, ldict, update_existing=True):
    """{'item','kind','status','reason'} - creates the level or, with
    update chosen, moves an existing one's elevation (moving a level
    moves what sits on it - the report says so)."""
    name = ldict.get("name") or "(unnamed)"
    row = {"item": name, "kind": "level", "status": "failed",
           "reason": ""}
    try:
        elev = float(ldict.get("elevation_ft"))
        existing = None
        for lvl in FilteredElementCollector(doc).OfClass(Level):
            if lvl.Name == name:
                existing = lvl
                break
        if existing is not None:
            if abs(existing.Elevation - elev) < 1e-9:
                row["status"] = "updated"
                row["reason"] = "already at this elevation"
                return row
            if not update_existing:
                row["status"] = "skipped"
                row["reason"] = "exists at a different elevation " \
                    "(skip existing chosen)"
                return row
            existing.Elevation = elev
            row["status"] = "updated"
            row["reason"] = "elevation MOVED to match the file - " \
                "everything hosted on it moved too"
            return row
        lvl = Level.Create(doc, elev)
        try:
            lvl.Name = name
        except Exception:
            row["status"] = "degraded"
            row["reason"] = "created but the name '{}' could not be " \
                "set".format(name)
            return row
        row["status"] = "created"
        return row
    except Exception as ex:
        row["reason"] = str(ex)
        return row


def import_fill_pattern(doc, d, update_existing=True):
    name = d.get("name") or "(unnamed)"
    row = {"item": name, "kind": "fill_pattern", "status": "failed",
           "reason": ""}
    try:
        target = getattr(FillPatternTarget, d.get("target") or
                         "Drafting")
        orientation = getattr(FillPatternHostOrientation,
                              d.get("orientation") or "ToView")
        pat = FillPattern(name, target, orientation)
        grids = List[FillGrid]()
        for g in d.get("grids") or []:
            fg = FillGrid()
            fg.Angle = float(g.get("angle") or 0.0)
            o = g.get("origin") or [0.0, 0.0]
            fg.Origin = UV(float(o[0]), float(o[1]))
            fg.Offset = float(g.get("offset") or 0.0)
            fg.Shift = float(g.get("shift") or 0.0)
            segs = List[float]()
            for s in g.get("segments") or []:
                segs.Add(float(s))
            fg.SetSegments(segs)
            grids.Add(fg)
        pat.SetFillGrids(grids)
        existing = None
        for fpe in FilteredElementCollector(doc).OfClass(
                FillPatternElement):
            try:
                p = fpe.GetFillPattern()
                if p.Name == name and p.Target == target:
                    existing = fpe
                    break
            except Exception:
                continue
        if existing is not None:
            if not update_existing:
                row["status"] = "skipped"
                row["reason"] = "already exists (skip existing chosen)"
                return row
            existing.SetFillPattern(pat)
            row["status"] = "updated"
            return row
        FillPatternElement.Create(doc, pat)
        row["status"] = "created"
        return row
    except Exception as ex:
        row["reason"] = str(ex)
        return row


def import_line_pattern(doc, d, update_existing=True):
    name = d.get("name") or "(unnamed)"
    row = {"item": name, "kind": "line_pattern", "status": "failed",
           "reason": ""}
    try:
        pat = LinePattern(name)
        segs = List[LinePatternSegment]()
        for s in d.get("segments") or []:
            stype = getattr(LinePatternSegmentType,
                            s.get("type") or "Dash")
            segs.Add(LinePatternSegment(stype,
                                        float(s.get("length") or 0.0)))
        pat.SetSegments(segs)
        existing = None
        for lpe in FilteredElementCollector(doc).OfClass(
                LinePatternElement):
            try:
                if lpe.Name == name:
                    existing = lpe
                    break
            except Exception:
                continue
        if existing is not None:
            if not update_existing:
                row["status"] = "skipped"
                row["reason"] = "already exists (skip existing chosen)"
                return row
            existing.SetLinePattern(pat)
            row["status"] = "updated"
            return row
        LinePatternElement.Create(doc, pat)
        row["status"] = "created"
        return row
    except Exception as ex:
        row["reason"] = str(ex)
        return row


def _lines_category(doc):
    want = bic_id("OST_Lines")
    for cat in doc.Settings.Categories:
        try:
            if id_value(cat.Id) == want:
                return cat
        except Exception:
            continue
    return None


def import_line_style(doc, d, update_existing=True):
    """{'item','kind','status','reason'} - creates or updates one line
    style (a Lines subcategory): projection weight, color and line
    pattern by name. A pattern missing from the model degrades that
    one setting with a note."""
    from Autodesk.Revit.DB import GraphicsStyleType
    name = d.get("name") or "(unnamed)"
    row = {"item": name, "kind": "line_style", "status": "failed",
           "reason": ""}
    notes = []
    try:
        lines_cat = _lines_category(doc)
        if lines_cat is None:
            row["reason"] = "no Lines category in this model"
            return row
        existing = None
        for sub in lines_cat.SubCategories:
            try:
                if sub.Name == name:
                    existing = sub
                    break
            except Exception:
                continue
        created = False
        if existing is None:
            existing = doc.Settings.Categories.NewSubcategory(
                lines_cat, name)
            created = True
        elif not update_existing:
            row["status"] = "skipped"
            row["reason"] = "already exists (skip existing chosen)"
            return row
        if d.get("weight") is not None:
            try:
                existing.SetLineWeight(int(d["weight"]),
                                       GraphicsStyleType.Projection)
            except Exception:
                notes.append("weight not applied")
        if d.get("color"):
            try:
                c = d["color"]
                existing.LineColor = Color(
                    System.Byte(c[0]), System.Byte(c[1]),
                    System.Byte(c[2]))
            except Exception:
                notes.append("color not applied")
        if d.get("pattern"):
            pid = line_pattern_by_name(doc, d["pattern"])
            if pid is None:
                notes.append("line pattern '{}' not in this "
                             "model".format(d["pattern"]))
            else:
                try:
                    existing.SetLinePatternId(
                        pid, GraphicsStyleType.Projection)
                except Exception:
                    notes.append("pattern not applied")
        row["status"] = "degraded" if notes else (
            "created" if created else "updated")
        row["reason"] = "; ".join(notes)
        return row
    except Exception as ex:
        row["reason"] = str(ex)
        return row


def filter_ids_by_name(doc):
    """{name: ElementId} of every rule-based filter in the model."""
    out = {}
    for pfe in FilteredElementCollector(doc).OfClass(
            ParameterFilterElement):
        try:
            out[pfe.Name] = pfe.Id
        except Exception:
            continue
    return out


def _ogs_pattern_misses(doc, ogs_dict, file_fills, file_lines, where,
                        out):
    if not ogs_dict:
        return
    for key in ("projection_lines", "cut_lines"):
        nm = (ogs_dict.get(key) or {}).get("pattern")
        if nm and nm not in file_lines and \
                line_pattern_by_name(doc, nm) is None:
            out.append(where + ("line pattern '{}' neither in the "
                                "file nor the model".format(nm),))
    for key in ("surface_fg", "surface_bg", "cut_fg", "cut_bg"):
        nm = (ogs_dict.get(key) or {}).get("pattern")
        if nm and nm not in file_fills and \
                fill_pattern_by_name(doc, nm) is None:
            out.append(where + ("fill pattern '{}' neither in the "
                                "file nor the model".format(nm),))


def preflight(doc, data):
    """DRY check of a loaded file against THIS model - nothing is
    modified. Returns [(kind, item, reason)] for everything that
    cannot import cleanly right now: unknown categories, missing
    shared / project parameters, unresolvable id-rule targets,
    templates with no way to be created, filters / patterns / levels
    / phase filters that are neither in the file nor the model. The
    real import stays fail-soft; this is the up-front view."""
    out = []
    from pymep_vt_schema import filters_used_by, family_label

    model_filters = set(filter_ids_by_name(doc).keys())
    file_filters = set()

    def walk(node, name):
        if not node:
            return
        if node.get("logic") == "rules":
            for r in node.get("rules") or []:
                try:
                    resolve_parameter(doc, r.get("parameter") or {})
                except Exception as ex:
                    out.append(("filter", name, str(ex)))
                if r.get("rule") == "element_id":
                    try:
                        resolve_element_ref(doc, r.get("value") or {})
                    except Exception as ex:
                        out.append(("filter", name, str(ex)))
        else:
            for c in node.get("children") or []:
                walk(c, name)

    for f in data.get("filters") or []:
        name = f.get("name") or "?"
        file_filters.add(name)
        for nm in f.get("categories") or []:
            if bic_id(nm) is None:
                out.append(("filter", name,
                            "category {} unknown in this "
                            "Revit".format(nm)))
        walk(f.get("element_filter"), name)

    file_fills = set(p.get("name")
                     for p in data.get("fill_patterns") or [])
    file_lines = set(p.get("name")
                     for p in data.get("line_patterns") or [])
    file_levels = set(l.get("name") for l in data.get("levels") or [])
    model_levels = set()
    for lvl in FilteredElementCollector(doc).OfClass(Level):
        try:
            model_levels.add(lvl.Name)
        except Exception:
            continue
    model_phase_filters = set()
    for pf in FilteredElementCollector(doc).OfClass(PhaseFilter):
        try:
            model_phase_filters.add(pf.Name)
        except Exception:
            continue

    for t in data.get("view_templates") or []:
        name = t.get("name") or "?"
        family = t.get("view_family") or ""
        existing = None
        for v in FilteredElementCollector(doc).OfClass(View):
            try:
                if v.IsTemplate and v.Name == name:
                    existing = v
                    break
            except Exception:
                continue
        if existing is not None:
            if str(existing.ViewType) != family:
                out.append(("template", name,
                            "a '{}' template of this name exists - "
                            "will be skipped".format(
                                family_label(str(existing.ViewType)))))
        else:
            if family in _PLAN_FAMILIES:
                if _view_family_type(doc, _PLAN_FAMILIES[family]) \
                        is None or _first_level(doc) is None:
                    out.append(("template", name,
                                "no {} view type / level to create "
                                "from".format(family_label(family))))
            elif family == "ThreeD":
                if _view_family_type(doc, "ThreeDimensional") is None:
                    out.append(("template", name,
                                "no 3D view type in this model"))
            elif family == "DraftingView":
                if _view_family_type(doc, "Drafting") is None:
                    out.append(("template", name,
                                "no drafting view type in this model"))
            else:
                donor = None
                for v in FilteredElementCollector(doc).OfClass(View):
                    try:
                        if not v.IsTemplate and \
                                str(v.ViewType) == family:
                            donor = v
                            break
                    except Exception:
                        continue
                if donor is None:
                    out.append(("template", name,
                                "needs one existing {} view as the "
                                "donor - none in this model".format(
                                    family_label(family))))
        for fname in filters_used_by(t):
            if fname not in file_filters and \
                    fname not in model_filters:
                out.append(("template", name,
                            "filter '{}' neither in the file nor "
                            "the model".format(fname)))
        for row in t.get("category_overrides") or []:
            _ogs_pattern_misses(doc, row.get("overrides"), file_fills,
                                file_lines, ("template", name), out)
        for row in t.get("filters") or []:
            _ogs_pattern_misses(doc, row.get("overrides"), file_fills,
                                file_lines, ("template", name), out)
        for entry in (t.get("view_range") or {}).values():
            lvl = entry.get("level")
            if lvl and lvl not in file_levels and \
                    lvl not in model_levels:
                out.append(("template", name,
                            "view range level '{}' neither in the "
                            "file nor the model".format(lvl)))
        pf = t.get("phase_filter")
        if pf and pf not in model_phase_filters:
            out.append(("template", name,
                        "phase filter '{}' not in this "
                        "model".format(pf)))

    for d in data.get("line_styles") or []:
        nm = d.get("pattern")
        if nm and nm not in file_lines and \
                line_pattern_by_name(doc, nm) is None:
            out.append(("line_style", d.get("name") or "?",
                        "line pattern '{}' neither in the file nor "
                        "the model".format(nm)))

    # dedupe, keep order
    seen = set()
    deduped = []
    for row in out:
        if row not in seen:
            seen.add(row)
            deduped.append(row)
    return deduped
