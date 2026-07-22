# -*- coding: utf-8 -*-
"""Engine for the Project Setup button: worksets, piping system types,
view filters and view templates, all driven by a JSON config.

Every project-specific value (names, colours, abbreviations) lives in
the config - nothing in this file names a project. Each stage is one
function taking (doc, config, results); results is a shared list of
(stage, item, status, detail) report rows. Every individual item is
wrapped so one bad entry never kills its stage, and running twice on
the same model yields all 'Skipped (exists)' rows (name compares are
case-insensitive).
"""

import json

import clr
clr.AddReference("RevitAPI")
clr.AddReference("System")

from System.Collections.Generic import List

from Autodesk.Revit.DB import (
    BuiltInCategory, BuiltInParameter, Color, ElementId,
    ElementParameterFilter, FillPatternElement, FilteredElementCollector,
    FilteredWorksetCollector, FilterRule, Level, MEPSystemClassification,
    OverrideGraphicSettings, ParameterFilterElement,
    ParameterFilterRuleFactory, Transaction, View, View3D,
    ViewDetailLevel, ViewDiscipline, ViewFamily, ViewFamilyType, ViewPlan,
    Workset, WorksetKind, WorksetVisibility,
)
from Autodesk.Revit.DB.Plumbing import PipingSystemType

CREATED = "Created"
SKIPPED = "Skipped (exists)"
FAILED = "Failed"
WARNING = "Warning"

EXPECTED_KEYS = ("worksets", "piping_systems", "filters", "view_templates")

# Pipe-relevant MEPSystemClassification members the config may name.
# VERIFY: exact member spellings against the installed API (RevitLookup >
# enum MEPSystemClassification): DomesticColdWater, DomesticHotWater,
# Sanitary, Vent, FireProtectWet, FireProtectDry, FireProtectPreaction,
# FireProtectOther, SupplyHydronic, ReturnHydronic, OtherPipe.


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------
def _row(results, stage, item, status, detail=""):
    results.append((stage, item, status, detail))


def _el_name(el):
    """Element name, tolerant of the IronPython .Name property clash."""
    try:
        return el.Name or ""
    except Exception:
        try:
            from Autodesk.Revit.DB import Element
            return Element.Name.__get__(el) or ""
        except Exception:
            return ""


def _key(name):
    return str(name or "").strip().lower()


def _enum_member(enum_type, name):
    """Case-insensitive enum member lookup; None when absent."""
    want = _key(name)
    for n in dir(enum_type):
        if n.startswith("_"):
            continue
        if n.lower() == want:
            try:
                return getattr(enum_type, n)
            except Exception:
                return None
    return None


def _color(rgb):
    return Color(int(rgb[0]), int(rgb[1]), int(rgb[2]))


def load_config(path):
    """Read + validate the JSON config. Raises ValueError with a
    readable message on any problem."""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except Exception as ex:
        raise ValueError("Could not read the config file:\n{}".format(ex))
    try:
        cfg = json.loads(raw.decode("utf-8-sig", "replace"))
    except Exception as ex:
        raise ValueError("The config is not valid JSON:\n{}".format(ex))
    if not isinstance(cfg, dict):
        raise ValueError("The config root must be a JSON object.")
    missing = [k for k in EXPECTED_KEYS if k not in cfg]
    if missing:
        raise ValueError(
            "The config is missing the top-level key(s): {}.".format(
                ", ".join(missing)))
    return cfg


# ---------------------------------------------------------------------------
# stage 1 - worksets
# ---------------------------------------------------------------------------
def stage_worksets(doc, config, results):
    stage = "Worksets"
    names = config.get("worksets") or []
    if not doc.IsWorkshared:
        _row(results, stage, "(all)", WARNING,
             "model is not workshared - stage skipped")
        return
    existing = set()
    for ws in FilteredWorksetCollector(doc).OfKind(WorksetKind.UserWorkset):
        existing.add(_key(ws.Name))
    t = Transaction(doc, "Project Setup - worksets")
    t.Start()
    try:
        for name in names:
            label = str(name)
            if _key(label) in existing:
                _row(results, stage, label, SKIPPED)
                continue
            try:
                Workset.Create(doc, label)
                existing.add(_key(label))
                _row(results, stage, label, CREATED)
            except Exception as ex:
                _row(results, stage, label, FAILED, str(ex))
        t.Commit()
    except Exception as ex:
        t.RollBack()
        _row(results, stage, "(stage)", FAILED, str(ex))


# ---------------------------------------------------------------------------
# stage 2 - piping system types (duplicate a donor of the same
# classification - PipingSystemType has no Create and SystemClassification
# is read-only, so donor + Duplicate is the only viable route)
# ---------------------------------------------------------------------------
def _set_abbreviation(new_type, abbr):
    """True when the abbreviation stuck via any route."""
    # VERIFY: MEPSystemType.Abbreviation property (read/write) exists in
    # 2023-2025; it is the cleanest route when present.
    try:
        new_type.Abbreviation = abbr
        return True
    except Exception:
        pass
    # VERIFY: RBS_SYSTEM_ABBREVIATION_PARAM is the abbreviation BIP that
    # applies to piping system TYPES (check the type in RevitLookup).
    for bip_name in ("RBS_SYSTEM_ABBREVIATION_PARAM",):
        bip = getattr(BuiltInParameter, bip_name, None)
        if bip is None:
            continue
        try:
            p = new_type.get_Parameter(bip)
            if p is not None and not p.IsReadOnly:
                p.Set(abbr)
                return True
        except Exception:
            pass
    try:
        p = new_type.LookupParameter("Abbreviation")
        if p is not None and not p.IsReadOnly:
            p.Set(abbr)
            return True
    except Exception:
        pass
    return False


def stage_piping_systems(doc, config, results):
    stage = "Piping systems"
    groups = config.get("piping_systems") or []

    existing = {}   # lower name -> PipingSystemType (any classification)
    donors = {}     # int(classification) -> first donor of that class
    for pst in FilteredElementCollector(doc).OfClass(PipingSystemType):
        nm = _key(_el_name(pst))
        if nm and nm not in existing:
            existing[nm] = pst
        try:
            ckey = int(pst.SystemClassification)
            if ckey not in donors:
                donors[ckey] = pst
        except Exception:
            pass

    t = Transaction(doc, "Project Setup - piping systems")
    t.Start()
    try:
        for grp in groups:
            cls_name = str(grp.get("classification") or "")
            systems = grp.get("systems") or []
            cls = _enum_member(MEPSystemClassification, cls_name)
            if cls is None:
                for s in systems:
                    _row(results, stage, str(s.get("name") or "?"), FAILED,
                         "unknown classification '{}'".format(cls_name))
                continue
            donor = donors.get(int(cls))
            if donor is None:
                for s in systems:
                    _row(results, stage, str(s.get("name") or "?"), FAILED,
                         "no donor system type of classification {} in "
                         "model - load one from the template".format(
                             cls_name))
                continue
            for s in systems:
                name = str(s.get("name") or "").strip()
                if not name:
                    _row(results, stage, "(unnamed)", FAILED,
                         "system entry with no name")
                    continue
                if _key(name) in existing:
                    _row(results, stage, name, SKIPPED)
                    continue
                try:
                    new_type = donor.Duplicate(name)
                    existing[_key(name)] = new_type
                    detail = ""
                    abbr = s.get("abbreviation")
                    if abbr and not _set_abbreviation(new_type, str(abbr)):
                        detail = ("created, but the abbreviation could "
                                  "not be set")
                    # Per-system "color": PipingSystemType exposes no
                    # clean line-colour API member; the view-template
                    # filter overrides are the primary colouring
                    # mechanism, so the config colour is left for those.
                    _row(results, stage, name, CREATED, detail)
                except Exception as ex:
                    _row(results, stage, name, FAILED, str(ex))
        t.Commit()
    except Exception as ex:
        t.RollBack()
        _row(results, stage, "(stage)", FAILED, str(ex))


# ---------------------------------------------------------------------------
# stage 3 - view filters
# ---------------------------------------------------------------------------
_TEXT_FACTORIES = {
    "equals": "CreateEqualsRule",
    "not_equals": "CreateNotEqualsRule",
    "contains": "CreateContainsRule",
    "not_contains": "CreateNotContainsRule",
    "begins_with": "CreateBeginsWithRule",
}
_NUM_FACTORIES = {
    "equals": "CreateEqualsRule",
    "greater": "CreateGreaterRule",
    "less": "CreateLessRule",
}


def _param_id(doc, pname):
    """ElementId for a rule's parameter string: BuiltInParameter member
    name first, else a project/shared parameter from the binding map."""
    bip = getattr(BuiltInParameter, str(pname or ""), None)
    if bip is not None:
        return ElementId(bip)
    try:
        it = doc.ParameterBindings.ForwardIterator()
        while it.MoveNext():
            d = it.Key
            try:
                if d is not None and _key(d.Name) == _key(pname):
                    return d.Id
            except Exception:
                continue
    except Exception:
        pass
    return None


def _text_rule(factory, pid, sval, v2023):
    # Revit 2023+ removed the trailing caseSensitive bool from the
    # string-rule factory methods; older builds require it. Branch on
    # the version, with a TypeError retry either way. # VERIFY: 2023+
    # two-arg signatures on the installed API.
    if v2023:
        try:
            return factory(pid, sval)
        except TypeError:
            return factory(pid, sval, False)
    try:
        return factory(pid, sval, False)
    except TypeError:
        return factory(pid, sval)


def _make_rule(pid, rule, value, v2023):
    r = _key(rule)
    if isinstance(value, bool):
        raise ValueError("boolean rule values are not supported")
    if isinstance(value, (int, float)):
        fac_name = _NUM_FACTORIES.get(r)
        if fac_name is None:
            raise ValueError(
                "rule '{}' is not supported for numbers".format(rule))
        factory = getattr(ParameterFilterRuleFactory, fac_name)
        if isinstance(value, int):
            return factory(pid, value)
        return factory(pid, float(value), 1e-6)
    fac_name = _TEXT_FACTORIES.get(r)
    if fac_name is None:
        raise ValueError("rule '{}' is not supported for text".format(rule))
    return _text_rule(getattr(ParameterFilterRuleFactory, fac_name),
                      pid, str(value), v2023)


def stage_filters(doc, config, results):
    stage = "View filters"
    entries = config.get("filters") or []
    existing = set()
    for f in FilteredElementCollector(doc).OfClass(ParameterFilterElement):
        existing.add(_key(_el_name(f)))
    v2023 = True
    try:
        v2023 = int(doc.Application.VersionNumber) >= 2023
    except Exception:
        pass
    t = Transaction(doc, "Project Setup - view filters")
    t.Start()
    try:
        for e in entries:
            name = str(e.get("name") or "").strip()
            if not name:
                _row(results, stage, "(unnamed)", FAILED,
                     "filter entry with no name")
                continue
            if _key(name) in existing:
                _row(results, stage, name, SKIPPED)
                continue
            try:
                cat_ids = List[ElementId]()
                for cname in e.get("categories") or []:
                    bic = getattr(BuiltInCategory, str(cname), None)
                    if bic is None:
                        raise ValueError(
                            "unknown category '{}'".format(cname))
                    cat_ids.Add(ElementId(bic))
                if cat_ids.Count == 0:
                    raise ValueError("no categories given")
                rules = List[FilterRule]()
                for rl in e.get("rules") or []:
                    pid = _param_id(doc, rl.get("parameter"))
                    if pid is None:
                        raise ValueError("unknown parameter '{}'".format(
                            rl.get("parameter")))
                    rules.Add(_make_rule(pid, rl.get("rule"),
                                         rl.get("value"), v2023))
                if rules.Count == 0:
                    raise ValueError("no rules given")
                # multiple rules AND together inside one
                # ElementParameterFilter (kept consistent throughout)
                ParameterFilterElement.Create(
                    doc, name, cat_ids, ElementParameterFilter(rules))
                existing.add(_key(name))
                _row(results, stage, name, CREATED)
            except Exception as ex:
                _row(results, stage, name, FAILED, str(ex))
        t.Commit()
    except Exception as ex:
        t.RollBack()
        _row(results, stage, "(stage)", FAILED, str(ex))


# ---------------------------------------------------------------------------
# stage 4 - view templates (no create-blank API: scratch view ->
# CreateViewTemplate -> rename -> delete the scratch)
# ---------------------------------------------------------------------------
def _first_vft(doc, fam):
    for vft in FilteredElementCollector(doc).OfClass(ViewFamilyType):
        try:
            if vft.ViewFamily == fam:
                return vft
        except Exception:
            continue
    return None


def _solid_fill_id(doc):
    """The solid FillPatternElement by IsSolidFill - never by the
    localised name 'Solid fill'."""
    for fp in FilteredElementCollector(doc).OfClass(FillPatternElement):
        try:
            if fp.GetFillPattern().IsSolidFill:
                return fp.Id
        except Exception:
            continue
    return None


def _apply_template_settings(doc, tmpl, e, filters_by_name, ws_ids,
                             solid_id, warnings):
    if e.get("scale"):
        try:
            tmpl.Scale = int(e["scale"])
        except Exception as ex:
            warnings.append("scale: {}".format(ex))
    dl = e.get("detail_level")
    if dl:
        m = _enum_member(ViewDetailLevel, dl)
        if m is None:
            warnings.append("unknown detail_level '{}'".format(dl))
        else:
            try:
                tmpl.DetailLevel = m
            except Exception as ex:
                warnings.append("detail_level: {}".format(ex))
    disc = e.get("discipline")
    if disc:
        m = _enum_member(ViewDiscipline, disc)
        if m is None:
            warnings.append("unknown discipline '{}'".format(disc))
        else:
            try:
                tmpl.Discipline = m
            except Exception as ex:
                warnings.append("discipline: {}".format(ex))

    for fe in e.get("filters") or []:
        fname = str(fe.get("filter") or "")
        pfe = filters_by_name.get(_key(fname))
        if pfe is None:
            warnings.append("filter '{}' not found".format(fname))
            continue
        try:
            try:
                tmpl.AddFilter(pfe.Id)
            except Exception:
                pass    # already applied to this view
            tmpl.SetFilterVisibility(pfe.Id, bool(fe.get("visible", True)))
            ogs = OverrideGraphicSettings()
            if fe.get("projection_line_color"):
                ogs.SetProjectionLineColor(
                    _color(fe["projection_line_color"]))
            if fe.get("cut_line_color"):
                ogs.SetCutLineColor(_color(fe["cut_line_color"]))
            if fe.get("solid_fill_color"):
                if solid_id is not None:
                    # VERIFY: SetSurfaceForegroundPatternId /
                    # ...PatternColor (2019+ foreground/background API).
                    ogs.SetSurfaceForegroundPatternId(solid_id)
                    ogs.SetSurfaceForegroundPatternColor(
                        _color(fe["solid_fill_color"]))
                else:
                    warnings.append("no solid fill pattern in the model")
            if fe.get("halftone") is not None:
                ogs.SetHalftone(bool(fe.get("halftone")))
            tmpl.SetFilterOverrides(pfe.Id, ogs)
        except Exception as ex:
            warnings.append("filter '{}': {}".format(fname, ex))

    # "solo_workset": ONLY this workset visible - every other user
    # workset in the model (not just config ones) is hidden. Used by the
    # dashboard-export flow's per-workset isolation templates.
    solo = e.get("solo_workset")
    if solo:
        if not doc.IsWorkshared:
            warnings.append("solo workset skipped - model not workshared")
        elif _key(solo) not in ws_ids:
            warnings.append("workset '{}' not found".format(solo))
        else:
            for wkey in ws_ids:
                vis = (WorksetVisibility.Visible if wkey == _key(solo)
                       else WorksetVisibility.Hidden)
                try:
                    tmpl.SetWorksetVisibility(ws_ids[wkey], vis)
                except Exception as ex:
                    warnings.append("workset '{}': {}".format(wkey, ex))

    wsv = e.get("workset_visibility") or {}
    if wsv and not doc.IsWorkshared:
        warnings.append("workset visibility skipped - model not workshared")
    elif wsv:
        for wname in wsv:
            wid = ws_ids.get(_key(wname))
            if wid is None:
                warnings.append("workset '{}' not found".format(wname))
                continue
            m = _enum_member(WorksetVisibility, wsv[wname])
            if m is None:
                warnings.append("unknown workset visibility '{}'".format(
                    wsv[wname]))
                continue
            try:
                # VERIFY: SetWorksetVisibility is accepted on a view
                # TEMPLATE (templates store workset visibility).
                tmpl.SetWorksetVisibility(wid, m)
            except Exception as ex:
                warnings.append("workset '{}': {}".format(wname, ex))


def stage_view_templates(doc, config, results):
    stage = "View templates"
    entries = config.get("view_templates") or []

    existing = set()
    for v in FilteredElementCollector(doc).OfClass(View):
        try:
            if v.IsTemplate:
                existing.add(_key(_el_name(v)))
        except Exception:
            continue
    filters_by_name = {}
    for f in FilteredElementCollector(doc).OfClass(ParameterFilterElement):
        filters_by_name[_key(_el_name(f))] = f
    ws_ids = {}
    if doc.IsWorkshared:
        for ws in FilteredWorksetCollector(doc).OfKind(
                WorksetKind.UserWorkset):
            ws_ids[_key(ws.Name)] = ws.Id
    solid_id = _solid_fill_id(doc)

    t = Transaction(doc, "Project Setup - view templates")
    t.Start()
    try:
        for e in entries:
            name = str(e.get("name") or "").strip()
            if not name:
                _row(results, stage, "(unnamed)", FAILED,
                     "template entry with no name")
                continue
            if _key(name) in existing:
                _row(results, stage, name, SKIPPED)
                continue
            scratch = None
            try:
                base = str(e.get("base_view_type") or "FloorPlan")
                if _key(base) == "threed":
                    vft = _first_vft(doc, ViewFamily.ThreeDimensional)
                    if vft is None:
                        raise ValueError("no 3D ViewFamilyType in model")
                    scratch = View3D.CreateIsometric(doc, vft.Id)
                else:
                    vft = _first_vft(doc, ViewFamily.FloorPlan)
                    lvl = None
                    for l in FilteredElementCollector(doc).OfClass(Level):
                        lvl = l
                        break
                    if vft is None or lvl is None:
                        raise ValueError("no floor-plan ViewFamilyType or "
                                         "no Level in the model")
                    scratch = ViewPlan.Create(doc, vft.Id, lvl.Id)

                # VERIFY: CreateViewTemplate return type - View on
                # 2023-2025 per docs; the isinstance guard covers an
                # ElementId return on older builds.
                res = scratch.CreateViewTemplate()
                tmpl = res if isinstance(res, View) else doc.GetElement(res)
                try:
                    tmpl.Name = name
                except Exception:
                    # never leave an orphan auto-named template behind
                    try:
                        doc.Delete(tmpl.Id)
                    except Exception:
                        pass
                    raise

                warnings = []
                _apply_template_settings(doc, tmpl, e, filters_by_name,
                                         ws_ids, solid_id, warnings)

                doc.Delete(scratch.Id)
                scratch = None
                existing.add(_key(name))
                _row(results, stage, name, CREATED, "; ".join(warnings))
            except Exception as ex:
                if scratch is not None:
                    try:
                        doc.Delete(scratch.Id)
                    except Exception:
                        pass
                _row(results, stage, name, FAILED, str(ex))
        t.Commit()
    except Exception as ex:
        t.RollBack()
        _row(results, stage, "(stage)", FAILED, str(ex))


# Fixed run order: filters can reference system names; templates
# reference filters and worksets.
STAGES = [
    ("Worksets", stage_worksets),
    ("Piping systems", stage_piping_systems),
    ("View filters", stage_filters),
    ("View templates", stage_view_templates),
]


# ---------------------------------------------------------------------------
# config built from a dashboard MODEL export (one piping system per pipe
# layer, exact layer names; worksets from the embedded workset_map)
# ---------------------------------------------------------------------------
PIPE_CLASSIFICATIONS = [
    "Sanitary", "Vent", "DomesticColdWater", "DomesticHotWater",
    "FireProtectWet", "FireProtectDry", "FireProtectPreaction",
    "FireProtectOther", "SupplyHydronic", "ReturnHydronic", "OtherPipe",
]


def config_from_model_export(path, classification="Sanitary",
                             fallback_workset_map=None):
    """Build a Project Setup config from a dashboard MODEL-*.json (or
    PIPES-*.json): one piping system per pipe layer, named EXACTLY like
    the layer; worksets (and one isolation view template per workset)
    from the export's embedded workset_map - falling back to
    ``fallback_workset_map`` ({layer: workset}, e.g. the locally saved
    dashboard map) when the export carries none. Raises ValueError with
    a readable message on anything unusable."""
    try:
        with open(path, "rb") as f:
            raw = f.read()
        data = json.loads(raw.decode("utf-8-sig", "replace"))
    except Exception as ex:
        raise ValueError("Could not read the export:\n{}".format(ex))
    if not isinstance(data, dict) or not isinstance(data.get("pipes"), list):
        raise ValueError(
            "This is not a dashboard model/pipes export - it has no "
            "'pipes' list (kind='{}').".format(
                data.get("kind") if isinstance(data, dict) else "?"))

    layers = []
    seen = set()
    for p in data.get("pipes") or []:
        try:
            lay = str(p.get("layer") or "").strip()
        except Exception:
            continue
        if lay and lay.lower() not in seen:
            seen.add(lay.lower())
            layers.append(lay)
    layers.sort(key=lambda s: s.lower())
    if not layers:
        raise ValueError("The export has no pipe layers to build piping "
                         "systems from.")

    worksets = []
    wmap = data.get("workset_map")
    if not (isinstance(wmap, dict) and wmap):
        wmap = fallback_workset_map
    if isinstance(wmap, dict):
        wseen = set()
        for v in wmap.values():
            nm = str(v or "").strip()
            if nm and nm.lower() not in wseen:
                wseen.add(nm.lower())
                worksets.append(nm)
        worksets.sort(key=lambda s: s.lower())

    return {
        "worksets": worksets,
        "piping_systems": [{
            "group": "Dashboard layers",
            "classification": classification,
            "systems": [{"name": lay} for lay in layers],
        }],
        "filters": [],
        # one isolation template per workset, named exactly after it:
        # ONLY that workset on, every other user workset hidden
        "view_templates": [
            {"name": ws, "base_view_type": "FloorPlan",
             "solo_workset": ws}
            for ws in worksets
        ],
    }
