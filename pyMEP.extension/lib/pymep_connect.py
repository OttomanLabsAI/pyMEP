# -*- coding: utf-8 -*-
"""Build elbow fittings between consecutive duct straights.

Entry point:
    build_connections(doc, bend_csv_path, log=None) -> (created, failed)

Finds all ducts in the active document whose Mark matches "C{col}-O{order}",
groups them by collection, orders them by Order number, and for every
consecutive pair inserts Revit's default elbow via NewElbowFitting. The
elbow's radius parameter is then set from the plan_bend_outlines CSV
(PipeBendRadius_mm), and its Mark becomes "C{col}-O{n}@{n+1}".

Revit picks the elbow family / symbol itself via the duct type's routing
preferences - no family name handling on our side.
"""

import re

import clr
clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import (
    BuiltInParameter, FilteredElementCollector, Transaction,
)
from Autodesk.Revit.DB.Mechanical import Duct

from pymep_revit import safe_name, mm2ft
from pymep_csv import read_csv_dicts


MARK_RE = re.compile(r"^C(\d+)-O(\d+)$")

# Elbow radius is exposed under several names depending on family. We try
# each in order. "Throat Fixed Radius" is the Sheet Metal rectangular elbow
# family; "Nominal Radius" is the generic one; "Radius" is a common custom
# one.
RADIUS_PARAM_NAMES = (
    "Throat Fixed Radius",
    "Nominal Radius",
    "Radius",
    "Bend Radius",
)


def _say(log, msg):
    if log is not None:
        log(msg)


def _get_mark(el):
    p = (el.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)
         or el.LookupParameter("Mark"))
    if p is None:
        return None
    return p.AsString()


def _set_mark(el, value):
    p = (el.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)
         or el.LookupParameter("Mark"))
    if p is not None and not p.IsReadOnly:
        p.Set(value)


def _set_radius(fitting, radius_mm):
    """Set whichever radius parameter the elbow exposes. Returns the
    parameter name that was set, or None if none worked."""
    for name in RADIUS_PARAM_NAMES:
        p = fitting.LookupParameter(name)
        if p is not None and not p.IsReadOnly:
            try:
                p.Set(mm2ft(radius_mm))
                return name
            except Exception:
                continue
    return None


def _end_connectors(duct):
    """Return the duct's two END connectors as (c1, c2). Skips any
    non-end connectors (e.g. mid-span taps)."""
    cm = duct.ConnectorManager
    ends = []
    it = cm.Connectors.ForwardIterator()
    while it.MoveNext():
        c = it.Current
        # ConnectorType.End is 0. Using string compare to avoid importing
        # the enum and keep this module lean.
        if str(c.ConnectorType) == "End":
            ends.append(c)
    return ends


def _nearest_connectors(duct_a, duct_b):
    """Return (conn_a, conn_b) - the end connector of each duct that's
    closest to the other duct."""
    ea = _end_connectors(duct_a)
    eb = _end_connectors(duct_b)
    if not ea or not eb:
        raise ValueError("duct has no end connectors")

    best = None
    for ca in ea:
        for cb in eb:
            d = ca.Origin.DistanceTo(cb.Origin)
            if best is None or d < best[0]:
                best = (d, ca, cb)
    return best[1], best[2]


def build_connections(doc, bend_csv_path, log=None):
    """Connect consecutive ducts via elbows, per collection. Returns
    (created, failed)."""

    # --- 1. Radius lookup ---------------------------------------------------
    # One entry per (collection, bend_idx): pipe bend radius in mm.
    bend_rows = read_csv_dicts(bend_csv_path)
    radii = {}   # (col_int, bend_idx_int) -> radius_mm
    for row in bend_rows:
        try:
            col  = int(row["Collection"])
            bidx = int(row["BendIdx"])
            r    = float(row["PipeBendRadius_mm"])
        except (KeyError, ValueError):
            continue
        radii[(col, bidx)] = r
    if not radii:
        raise ValueError(
            "No PipeBendRadius_mm values found in the plan_bend_outlines CSV. "
            "Re-run Export Pipework Data so the column is written.")
    _say(log, "Loaded **{}** bend radii from plan_bend_outlines CSV.".format(len(radii)))

    # --- 2. Collect ducts by Mark ------------------------------------------
    # Mark format: "C{col}-O{order}"
    by_col = {}  # col_int -> list of (order_int, duct)
    for d in FilteredElementCollector(doc).OfClass(Duct):
        mark = _get_mark(d)
        if not mark:
            continue
        m = MARK_RE.match(mark.strip())
        if not m:
            continue
        col   = int(m.group(1))
        order = int(m.group(2))
        by_col.setdefault(col, []).append((order, d))
    if not by_col:
        raise ValueError(
            "No ducts with a 'C#-O#' Mark found. Run Build Ducts first.")
    total_ducts = sum(len(v) for v in by_col.values())
    _say(log, "Found **{}** pyMEP ducts across **{}** collection(s).".format(
        total_ducts, len(by_col)))

    # --- 3. Per collection, walk consecutive pairs -------------------------
    # Bends within a collection are ordered by bend_idx. Pair i (0-indexed)
    # connects Order i+1 to Order i+2, and uses bend radius at the
    # i-th bend_idx (sorted ascending).
    created = 0
    failed  = 0

    with Transaction(doc, "Build Connections") as t:
        t.Start()
        for col in sorted(by_col):
            segs = sorted(by_col[col], key=lambda p: p[0])
            # Bend radii for this collection, ordered by bend_idx ascending
            col_bend_indices = sorted(
                bidx for (c, bidx) in radii if c == col)
            n_pairs = len(segs) - 1
            if n_pairs <= 0:
                _say(log, "Collection {} has only {} duct(s) - nothing to connect."
                          .format(col, len(segs)))
                continue
            if len(col_bend_indices) < n_pairs:
                _say(log, "**Collection {}: warning** - {} pair(s) but only "
                          "{} bend radius entries.".format(
                              col, n_pairs, len(col_bend_indices)))

            for i in range(n_pairs):
                (oa, duct_a), (ob, duct_b) = segs[i], segs[i + 1]
                tag = "C{}-O{}@{}".format(col, oa, ob)
                try:
                    ca, cb = _nearest_connectors(duct_a, duct_b)
                    elbow = doc.Create.NewElbowFitting(ca, cb)
                    if elbow is None:
                        raise ValueError("NewElbowFitting returned None")
                    # Let the new fitting settle so its connectors inherit the
                    # ducts' width/height before we touch the radius. Without a
                    # regenerate the fitting can report stale geometry.
                    doc.Regenerate()

                    # Set radius from the corresponding bend in this collection
                    if i < len(col_bend_indices):
                        r_mm = radii[(col, col_bend_indices[i])]
                        set_name = _set_radius(elbow, r_mm)
                        doc.Regenerate()
                        if set_name:
                            _say(log, "  {}: elbow OK, {} = {:.0f} mm".format(
                                tag, set_name, r_mm))
                        else:
                            _say(log, "  {}: elbow OK, **no radius param found** "
                                      "(tried {})".format(
                                          tag, ", ".join(RADIUS_PARAM_NAMES)))
                    else:
                        _say(log, "  {}: elbow OK, no bend radius available".format(tag))

                    _set_mark(elbow, tag)
                    created += 1

                except Exception as ex:
                    failed += 1
                    _say(log, "  {}: **FAILED** - {}".format(tag, ex))

        t.Commit()

    return created, failed
