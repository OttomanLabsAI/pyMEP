# -*- coding: utf-8 -*-
"""Add pipe sizes to a Revit pipe Segment from LandXML diameters.

A "pipe size" in Revit lives on a ``PipeSegment`` (Autodesk.Revit.DB.
Plumbing), not on the ``PipeType``. Each segment carries a list of
``MEPSize`` entries (nominal / inner / outer diameter, all in internal
feet). Making a new size selectable in the type's size dropdown is simply
a matter of adding an ``MEPSize`` to the segment that the relevant
``PipeType`` already routes through.

Entry points
------------
``list_pipe_segments(doc)``
    -> [(name, PipeSegment), ...] for the picker.

``existing_segment_sizes_mm(segment)``
    -> sorted [nominal_mm, ...] already on the segment (for the preview).

``add_sizes_to_segment(doc, segment, sizes, log=None)``
    sizes: iterable of dicts with keys ``nominal_mm``, ``inner_mm``,
    ``outer_mm`` (as produced by
    ``pymep_landxml.distinct_circular_sizes``). Adds the ones not already
    present, inside a single transaction. Returns (added, skipped_existing,
    failed).

IronPython 2.7 / Revit 2021-2026 safe. ``Segment.AddSize`` and the
``MEPSize`` constructor have been stable across these versions.
"""

import clr
clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import (
    FilteredElementCollector, Transaction,
)
from Autodesk.Revit.DB.Plumbing import PipeSegment
# MEPSize lives in Autodesk.Revit.DB (not .Plumbing)
from Autodesk.Revit.DB import MEPSize

from pymep_revit import safe_name, mm2ft, ft2mm


_SIZE_TOL_FT = mm2ft(0.5)   # treat sizes within 0.5 mm as identical


def list_pipe_segments(doc):
    """Return [(name, PipeSegment), ...] sorted by name."""
    segs = []
    for seg in FilteredElementCollector(doc).OfClass(PipeSegment):
        try:
            segs.append((safe_name(seg), seg))
        except Exception:
            continue
    segs.sort(key=lambda t: t[0].lower())
    return segs


def _iter_sizes(segment):
    """Yield the segment's MEPSize objects across API variants."""
    try:
        sizes = segment.GetSizes()
    except Exception:
        sizes = None
    if sizes is None:
        return
    for s in sizes:
        yield s


def existing_segment_sizes_mm(segment):
    """Sorted list of nominal sizes (mm) already defined on the segment."""
    out = []
    for s in _iter_sizes(segment):
        try:
            out.append(round(ft2mm(s.NominalDiameter), 2))
        except Exception:
            continue
    out.sort()
    return out


def _has_nominal(segment, nominal_ft):
    for s in _iter_sizes(segment):
        try:
            if abs(s.NominalDiameter - nominal_ft) <= _SIZE_TOL_FT:
                return True
        except Exception:
            continue
    return False


def _make_mepsize(nominal_ft, inner_ft, outer_ft):
    """Construct an MEPSize across constructor signatures.

    Revit's documented ctor is:
        MEPSize(nominalDiameter, innerDiameter, outerDiameter,
                usedInSizeLists, usedInSizing)
    Some builds expose a 3-arg overload. Try the full one first, then
    fall back so the button doesn't die on a signature mismatch.
    """
    try:
        return MEPSize(nominal_ft, inner_ft, outer_ft, True, True)
    except Exception:
        pass
    return MEPSize(nominal_ft, inner_ft, outer_ft)


def add_sizes_to_segment(doc, segment, sizes, log=None):
    """Add each size in ``sizes`` to ``segment`` if not already present.

    sizes: iterable of dicts with nominal_mm / inner_mm / outer_mm.
    Returns (added, skipped_existing, failed). Wrapped in one transaction.
    """
    def say(m):
        if log is not None:
            log(m)

    # De-duplicate the incoming list by nominal (the LandXML can carry the
    # same bore with different wall thicknesses; for the size list the
    # nominal is what must be unique). Keep the FIRST occurrence, which is
    # the most common wall because the size list is built sorted/by count
    # upstream - but to be safe, prefer the entry with the largest count
    # if present.
    by_nominal = {}
    for s in sizes:
        nm = round(float(s["nominal_mm"]), 2)
        if nm <= 0:
            continue
        prev = by_nominal.get(nm)
        if prev is None or s.get("count", 0) > prev.get("count", 0):
            by_nominal[nm] = s
    wanted = [by_nominal[k] for k in sorted(by_nominal.keys())]

    seg_name = safe_name(segment)
    say("Target segment: **{}**".format(seg_name))
    say("Distinct nominal sizes to ensure: **{}** ({})".format(
        len(wanted), ", ".join("{:.0f}".format(w["nominal_mm"]) for w in wanted)))

    added = 0
    skipped = 0
    failed = 0

    with Transaction(doc, "LandXML: add pipe sizes") as t:
        t.Start()
        for w in wanted:
            nominal_mm = float(w["nominal_mm"])
            inner_mm = float(w.get("inner_mm", nominal_mm))
            outer_mm = float(w.get("outer_mm", nominal_mm))
            # Revit requires inner <= nominal <= outer to be sane; clamp the
            # obviously-bad cases (e.g. corrupt outer < inner) so AddSize
            # doesn't throw. If outer <= inner, set outer = inner (no wall).
            if outer_mm < inner_mm:
                outer_mm = inner_mm

            nominal_ft = mm2ft(nominal_mm)
            inner_ft = mm2ft(inner_mm)
            outer_ft = mm2ft(outer_mm)

            try:
                if _has_nominal(segment, nominal_ft):
                    skipped += 1
                    say("  - {:.0f} mm: already present, skipped"
                        .format(nominal_mm))
                    continue
                mep = _make_mepsize(nominal_ft, inner_ft, outer_ft)
                segment.AddSize(mep)
                added += 1
                say("  + {:.0f} mm  (ID {:.0f} / OD {:.0f}): added"
                    .format(nominal_mm, inner_mm, outer_mm))
            except Exception as ex:
                failed += 1
                say("  ! {:.0f} mm: FAILED - {}".format(nominal_mm, ex))
        t.Commit()

    say("Done - added **{}**, already-present **{}**, failed **{}**."
        .format(added, skipped, failed))
    return added, skipped, failed
