# -*- coding: utf-8 -*-
"""Replace a cylinder structure (a Generic Cylinder Plumbing Fixture with
DIA + H parameters, standing in for a vertical pipe) with a real Revit
pipe of the SAME diameter and length, at the same place, carrying the
same system type - then delete the original.

The cylinder is vertical: its bore is the DIA parameter, its length the
H parameter. The replacement pipe spans the cylinder's exact vertical
extent - XY from the instance location, base Z from the instance's
bounding box (robust to whatever vertical origin the family uses), top
one H above the base - so it lands exactly where the cylinder was.

Pure geometry at the top (unit-tested under CPython by
``tests/test_replace_structure.py``); Revit API access below.
IronPython 2.7 / Revit 2021-2026 safe.
"""

import clr
clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import (
    BuiltInParameter, ElementId, Transaction, XYZ, Level,
    FilteredElementCollector, FailureProcessingResult, FailureSeverity,
    IFailuresPreprocessor,
)
from Autodesk.Revit.DB.Plumbing import Pipe, PipeType, PipingSystemType

from pymep_revit import safe_name, ft2mm


DIA_NAMES = ["DIA", "Diameter", "dia", "Nominal Diameter", "D"]
H_NAMES = ["H", "Height", "h", "Length", "Depth"]


# ---------------------------------------------------------------------------
# pure geometry (stdlib only - unit-tested without Revit)
# ---------------------------------------------------------------------------
def vertical_endpoints(x, y, base_z, height_ft):
    """The pipe's two ends: straight up from (x, y, base_z) by
    ``height_ft``. Returns ((x, y, z0), (x, y, z1)) with the EXACT
    input coordinates - nothing rounded."""
    return ((x, y, base_z), (x, y, base_z + height_ft))


# ---------------------------------------------------------------------------
# Revit API access
# ---------------------------------------------------------------------------
def _named_double(inst, names):
    for nm in names:
        try:
            p = inst.LookupParameter(nm)
            if p is not None and p.HasValue and str(p.StorageType) == "Double":
                return p.AsDouble()
        except Exception:
            pass
    return None


def _system_type_id(inst):
    """The instance's piping System Type id, or None."""
    bip = getattr(BuiltInParameter, "RBS_PIPING_SYSTEM_TYPE_PARAM", None)
    if bip is not None:
        try:
            p = inst.get_Parameter(bip)
            if p is not None and str(p.StorageType) == "ElementId":
                eid = p.AsElementId()
                if eid is not None and eid != ElementId.InvalidElementId:
                    return eid
        except Exception:
            pass
    try:
        p = inst.LookupParameter("System Type")
        if p is not None and str(p.StorageType) == "ElementId":
            eid = p.AsElementId()
            if eid is not None and eid != ElementId.InvalidElementId:
                return eid
    except Exception:
        pass
    return None


def read_cylinder(inst):
    """Everything needed to rebuild one cylinder as a pipe, or None with
    a reason. Returns ``(info, None)`` or ``(None, reason)`` where info
    has dia_ft, height_ft, x, y, base_z, system_type_id, level_id,
    mark, comments."""
    dia = _named_double(inst, DIA_NAMES)
    h = _named_double(inst, H_NAMES)
    if dia is None or h is None:
        return None, ("'{}' has no {}/{} parameter - not a DIA/H "
                      "cylinder".format(
                          safe_name(inst),
                          "DIA" if dia is None else "",
                          "H" if h is None else "")).replace("//", "/")
    if dia <= 0 or h <= 0:
        return None, "'{}' has a zero DIA or H".format(safe_name(inst))

    loc = None
    try:
        loc = inst.Location.Point
    except Exception:
        pass
    bb = None
    try:
        bb = inst.get_BoundingBox(None)
    except Exception:
        pass
    if loc is not None:
        x, y = loc.X, loc.Y
    elif bb is not None:
        x = (bb.Min.X + bb.Max.X) / 2.0
        y = (bb.Min.Y + bb.Max.Y) / 2.0
    else:
        return None, "'{}' has no location".format(safe_name(inst))
    base_z = bb.Min.Z if bb is not None else (loc.Z if loc is not None else 0.0)

    level_id = None
    try:
        lid = inst.LevelId
        if lid is not None and lid != ElementId.InvalidElementId:
            level_id = lid
    except Exception:
        pass

    def _str(bip_name):
        bip = getattr(BuiltInParameter, bip_name, None)
        if bip is None:
            return ""
        try:
            p = inst.get_Parameter(bip)
            if p is not None and p.HasValue:
                return p.AsString() or ""
        except Exception:
            pass
        return ""

    return ({"dia_ft": dia, "height_ft": h, "x": x, "y": y,
             "base_z": base_z, "system_type_id": _system_type_id(inst),
             "level_id": level_id,
             "mark": _str("ALL_MODEL_MARK"),
             "comments": _str("ALL_MODEL_INSTANCE_COMMENTS")}, None)


def resolve_pipe_type(doc, preferred_name=None):
    """A PipeType to build with: the preferred one by name, else the
    first in the model. None when the model has no pipe types."""
    if preferred_name:
        for pt in FilteredElementCollector(doc).OfClass(PipeType):
            if safe_name(pt) == preferred_name:
                return pt
    for pt in FilteredElementCollector(doc).OfClass(PipeType):
        return pt
    return None


def _first_level_id(doc):
    for l in FilteredElementCollector(doc).OfClass(Level):
        return l.Id
    return None


def _first_system_type_id(doc):
    for st in FilteredElementCollector(doc).OfClass(PipingSystemType):
        return st.Id
    return None


class _SwallowWarnings(IFailuresPreprocessor):
    """Delete Revit's warning dialogs as they arrive. Errors still stop
    the transaction - only warnings ('elements have duplicate Mark
    values', 'pipe is slightly off axis' and friends) are dismissed, so
    a batch of replacements runs start to finish without a single
    click."""

    def PreprocessFailures(self, accessor):
        for f in accessor.GetFailureMessages():
            if f.GetSeverity() == FailureSeverity.Warning:
                accessor.DeleteWarning(f)
        return FailureProcessingResult.Continue


def _quiet(t):
    """Point a transaction at the warning swallower."""
    try:
        opts = t.GetFailureHandlingOptions()
        opts.SetFailuresPreprocessor(_SwallowWarnings())
        opts.SetClearAfterRollback(True)
        opts.SetDelayedMiniWarnings(True)
        t.SetFailureHandlingOptions(opts)
    except Exception:
        pass


def _build_pipe(doc, inst, pipe_type):
    """The replacement itself, WITHOUT a transaction of its own: build
    the pipe, carry the cylinder's data over, delete the cylinder.
    Returns (summary, old_name, info) or raises. The caller owns the
    transaction, so many of these can share one."""
    info, reason = read_cylinder(inst)
    if info is None:
        raise ValueError(reason)

    sys_id = info["system_type_id"] or _first_system_type_id(doc)
    if sys_id is None:
        raise RuntimeError("No piping system type available to build the "
                           "pipe (the cylinder has none and the model "
                           "defines none).")
    lvl_id = info["level_id"] or _first_level_id(doc)
    if lvl_id is None:
        raise RuntimeError("The model has no levels - cannot create a pipe.")

    p0, p1 = vertical_endpoints(info["x"], info["y"], info["base_z"],
                                info["height_ft"])
    # capture the name NOW - after doc.Delete + commit the instance is
    # gone and reading its .Name throws "referenced object is not valid"
    old_id = inst.Id
    old_name = safe_name(inst)

    pipe = Pipe.Create(doc, sys_id, pipe_type.Id, lvl_id,
                       XYZ(p0[0], p0[1], p0[2]),
                       XYZ(p1[0], p1[1], p1[2]))
    dp = pipe.get_Parameter(BuiltInParameter.RBS_PIPE_DIAMETER_PARAM)
    if dp is not None and not dp.IsReadOnly:
        dp.Set(info["dia_ft"])
    for bip_name, val in (("ALL_MODEL_MARK", info["mark"]),
                          ("ALL_MODEL_INSTANCE_COMMENTS",
                           info["comments"])):
        if not val:
            continue
        bip = getattr(BuiltInParameter, bip_name, None)
        if bip is None:
            continue
        try:
            q = pipe.get_Parameter(bip)
            if q is not None and not q.IsReadOnly:
                q.Set(val)
        except Exception:
            pass
    doc.Delete(old_id)

    return ({"new_id": pipe.Id, "dia_ft": info["dia_ft"],
             "height_ft": info["height_ft"]}, old_name, info)


def _line(old_name, info):
    return ("  {} (DIA {:.0f}, H {:.0f} mm) -> pipe {:.0f} mm x {:.0f} mm "
            "long".format(old_name, ft2mm(info["dia_ft"]),
                          ft2mm(info["height_ft"]), ft2mm(info["dia_ft"]),
                          ft2mm(info["height_ft"])))


def replace_with_pipe(doc, inst, pipe_type, log=None):
    """Replace ONE cylinder instance with a vertical pipe of the same
    diameter and length, in its own transaction. Returns a summary
    dict, or raises (transaction rolled back, original untouched)."""
    t = Transaction(doc, "Replace structure with pipe")
    _quiet(t)
    t.Start()
    try:
        out, old_name, info = _build_pipe(doc, inst, pipe_type)
        t.Commit()
    except Exception:
        t.RollBack()
        raise

    if log is not None:
        log(_line(old_name, info))
    return out


def replace_all_with_pipes(doc, instances, pipe_type, log=None):
    """Replace EVERY cylinder in ``instances`` in ONE transaction - one
    undo step, and Revit's warning dialogs are dismissed as they come,
    so a big selection never stops to ask.

    A cylinder that cannot be read or built is reported and skipped;
    the rest still land. Returns {"done", "failed", "results"}. Raises
    only when the whole transaction fails (rolled back, model
    untouched).
    """
    def say(m):
        if log is not None:
            log(m)

    results = []
    failures = []

    t = Transaction(doc, "Replace structures with pipes")
    _quiet(t)
    t.Start()
    try:
        for inst in instances:
            name = safe_name(inst)
            try:
                out, old_name, info = _build_pipe(doc, inst, pipe_type)
            except Exception as ex:
                failures.append((name, str(ex)))
                say("  ! {} not replaced: {}".format(name, ex))
                continue
            results.append(out)
            say(_line(old_name, info))
        t.Commit()
    except Exception:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        raise

    return {"done": len(results), "failed": len(failures),
            "failures": failures, "results": results}
