# -*- coding: utf-8 -*-
"""Place a family instance at the top of each selected element - and,
optionally, delete that element afterwards.

The inverse of Structure to Pipe: where that turns a placeholder
cylinder into a real pipe, this drops a real family (a chamber, a
gully, a cover, a node) onto the top of pipes, conduits and other
families that came in from a Civil 3D conversion.

'Top' depends on what was selected. Anything drawn as a line - pipe,
conduit, duct, cable tray - gives the higher of its two endpoints, so a
vertical riser gets the family on its head and a graded run gets it on
its upstream end. A placed family gives its own XY with the top of its
bounding box, so the family lands on its lid whatever its origin.

The instance lands at that exact XYZ: it is placed on the host's own
level and then nudged vertically to the point, which works whatever
vertical origin the family uses.

The geometry and the picker list-shaping at the top are pure stdlib and
unit-tested under CPython by ``tests/test_pipe_to_family.py``; Revit API
access lives below. IronPython 2.7 / Revit 2021-2026 safe.
"""

import clr
clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import (
    BuiltInParameter, ElementId, ElementTransformUtils, FamilyInstance,
    FamilySymbol, FilteredElementCollector, Level, MEPCurve, Transaction,
    XYZ,
)
from Autodesk.Revit.DB.Structure import StructuralType

from pymep_revit import safe_name


# a nudge smaller than this is not worth a move
MOVE_TOL_FT = 1e-9


# ---------------------------------------------------------------------------
# pure geometry / list shaping (stdlib only - unit-tested without Revit)
# ---------------------------------------------------------------------------
def top_point(p0, p1):
    """The higher of two (x, y, z) endpoints - a line element's top.
    Ties go to the first, so a flat pipe keeps its start end."""
    return p0 if p0[2] >= p1[2] else p1


def box_top(x, y, min_z, max_z):
    """The top of a placed family: its own XY at the highest point of
    its bounding box. A zero-height box (an origin-only family) gives
    the point itself, never something below it."""
    return (x, y, max_z if max_z >= min_z else min_z)


def symbol_categories(rows):
    """Sorted category names present in the picker rows."""
    return sorted(set(r["cat"] for r in rows), key=lambda s: s.lower())


def symbol_families(rows, cat):
    """Sorted family names inside one category."""
    return sorted(set(r["fam"] for r in rows if r["cat"] == cat),
                  key=lambda s: s.lower())


def symbol_types_in(rows, cat, fam):
    """The rows of one category + family, in type order."""
    return [r for r in rows if r["cat"] == cat and r["fam"] == fam]


def search_symbol_rows(rows, query):
    """Rows whose 'Category : Family : Type' label contains EVERY
    whitespace-separated word of ``query`` (case-insensitive). An empty
    query gives [] so the caller can fall back to the cascade."""
    words = (query or "").lower().split()
    if not words:
        return []
    return [r for r in rows if all(w in r["label"].lower() for w in words)]


def placement_summary(placed, deleted, failed):
    """The one-line result sentence, built the same way everywhere."""
    msg = "Placed {} famil{} on top.".format(
        placed, "y" if placed == 1 else "ies")
    if deleted:
        msg += " {} original(s) deleted.".format(deleted)
    if failed:
        msg += " {} failed - see the report.".format(failed)
    return msg


# ---------------------------------------------------------------------------
# Revit API access
# ---------------------------------------------------------------------------
def selected_hosts(doc, uidoc):
    """What the button can put a family on top of, in selection order:
    anything drawn as a line (pipe, conduit, duct, cable tray) and any
    placed family."""
    out = []
    for eid in uidoc.Selection.GetElementIds():
        el = doc.GetElement(eid)
        if isinstance(el, MEPCurve) or isinstance(el, FamilyInstance):
            out.append(el)
    return out


def curve_ends(el):
    """A line element's two endpoints as (x, y, z) tuples, or None when
    it is not curve-driven (a placed family, say)."""
    try:
        crv = el.Location.Curve
        a, b = crv.GetEndPoint(0), crv.GetEndPoint(1)
        return (a.X, a.Y, a.Z), (b.X, b.Y, b.Z)
    except Exception:
        return None


def element_top(el):
    """The point a family should land on, or None when the element
    gives up neither a curve nor a box: the higher end of a line
    element, or a placed family's own XY at the top of its box."""
    ends = curve_ends(el)
    if ends is not None:
        return top_point(ends[0], ends[1])

    bb = None
    try:
        bb = el.get_BoundingBox(None)
    except Exception:
        pass
    pt = None
    try:
        pt = el.Location.Point
    except Exception:
        pass
    if bb is None:
        return (pt.X, pt.Y, pt.Z) if pt is not None else None
    x = pt.X if pt is not None else (bb.Min.X + bb.Max.X) / 2.0
    y = pt.Y if pt is not None else (bb.Min.Y + bb.Max.Y) / 2.0
    return box_top(x, y, bb.Min.Z, bb.Max.Z)


def host_level_id(doc, el):
    """The element's own level id, else the model's first level."""
    for getter in (lambda: el.ReferenceLevel.Id,
                   lambda: el.get_Parameter(
                       BuiltInParameter.RBS_START_LEVEL_PARAM).AsElementId(),
                   lambda: el.LevelId):
        try:
            lid = getter()
            if lid is not None and lid != ElementId.InvalidElementId:
                return lid
        except Exception:
            continue
    for lvl in FilteredElementCollector(doc).OfClass(Level):
        return lvl.Id
    return None


def symbol_rows(doc):
    """One row per loaded, placeable family type:
    [{"cat", "fam", "type", "label", "id"}, ...] sorted by category,
    family, type. ``label`` is 'Category : Family : Type'."""
    rows = []
    for sym in FilteredElementCollector(doc).OfClass(FamilySymbol):
        cat, fam = "(no category)", "?"
        try:
            c = sym.Category
            if c is None:
                continue          # system types with no category
            # only model families can be placed in the model
            if str(getattr(c, "CategoryType", "Model")) != "Model":
                continue
            cat = c.Name
        except Exception:
            continue
        try:
            fam = safe_name(sym.Family)
        except Exception:
            pass
        typ = safe_name(sym)
        rows.append({"cat": cat, "fam": fam, "type": typ,
                     "label": "{} : {} : {}".format(cat, fam, typ),
                     "id": sym.Id})
    rows.sort(key=lambda r: (r["cat"].lower(), r["fam"].lower(),
                             r["type"].lower()))
    return rows


def _activate(doc, sym):
    """A family symbol must be active before it can be placed."""
    if not sym.IsActive:
        sym.Activate()
        doc.Regenerate()


def place_at_tops(doc, hosts, symbol_id, delete_hosts=False, log=None):
    """Place ``symbol_id`` on top of every element in ``hosts`` - pipes,
    conduits and placed families alike - in ONE transaction, optionally
    deleting those elements afterwards.

    Returns {"placed", "deleted", "failed", "instances"}. Raises with
    the transaction rolled back - the model is left untouched.
    """
    def say(m):
        if log is not None:
            log(m)

    sym = doc.GetElement(symbol_id)
    if not isinstance(sym, FamilySymbol):
        raise ValueError("That family type is no longer in the model.")

    placed, failed = [], 0
    to_delete = []

    t = Transaction(doc, "Place family at pipe tops")
    t.Start()
    try:
        _activate(doc, sym)
        for host in hosts:
            name = safe_name(host)
            top = element_top(host)
            if top is None:
                failed += 1
                say("  ! {}: no geometry to sit on - skipped".format(name))
                continue
            tx, ty, tz = top
            lvl_id = host_level_id(doc, host)
            if lvl_id is None:
                failed += 1
                say("  ! {}: the model has no level to place on"
                    .format(name))
                continue
            lvl = doc.GetElement(lvl_id)
            try:
                inst = doc.Create.NewFamilyInstance(
                    XYZ(tx, ty, tz), sym, lvl, StructuralType.NonStructural)
            except Exception as ex:
                # one awkward family/host must not lose the whole run
                failed += 1
                say("  ! {}: {} would not place here ({})".format(
                    name, safe_name(sym), ex))
                continue
            # NewFamilyInstance drops level-based families at their
            # level, not at the point - nudge it onto the host's top.
            doc.Regenerate()
            try:
                loc = inst.Location.Point
                dz = tz - loc.Z
                if abs(dz) > MOVE_TOL_FT:
                    ElementTransformUtils.MoveElement(doc, inst.Id,
                                                      XYZ(0, 0, dz))
            except Exception:
                pass
            placed.append(inst)
            say("- {} -> **{}** on its top".format(name, safe_name(sym)))
            if delete_hosts:
                to_delete.append(host.Id)

        for eid in to_delete:
            doc.Delete(eid)
        t.Commit()
    except Exception:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        raise

    return {"placed": len(placed), "deleted": len(to_delete),
            "failed": failed, "instances": placed}
