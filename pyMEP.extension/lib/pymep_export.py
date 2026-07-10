# -*- coding: utf-8 -*-
"""Export pipework data from a Revit selection to two CSVs.

Call `export_pipework(doc, uidoc, output_folder, log=None)` to run the full
workflow. Returns a summary dict with keys 'timestamp', 'folder', 'files',
'pipe_count' and 'fit_count'.
"""

import os
import datetime

from pymep_revit import (
    ft2mm, safe_name, get_connectors,
    get_param_mm, get_od, get_id,
    arc_from_connectors, get_bend_angle,
    PIPE_CATS, FIT_CATS,
)
from pymep_csv import write_csv, fmt


def _pipe_row(e, doc):
    eid = e.Id.IntegerValue
    try:
        tname = safe_name(doc.GetElement(e.GetTypeId()))
    except Exception:
        tname = "?"
    conns = get_connectors(e)
    od = get_od(e, conns)
    id_val = get_id(e)
    loc = e.Location
    if loc and hasattr(loc, "Curve") and loc.Curve:
        crv = loc.Curve
        sp = crv.GetEndPoint(0); ep = crv.GetEndPoint(1)
        length = ft2mm(crv.Length)
    elif len(conns) >= 2:
        sp = conns[0].Origin; ep = conns[1].Origin
        length = ft2mm(sp.DistanceTo(ep))
    else:
        return None
    return "{},{},{},{},{},{},{},{},{},{},{}".format(
        eid, tname,
        fmt(ft2mm(sp.X)), fmt(ft2mm(sp.Y)), fmt(ft2mm(sp.Z)),
        fmt(ft2mm(ep.X)), fmt(ft2mm(ep.Y)), fmt(ft2mm(ep.Z)),
        fmt(length), fmt(od), fmt(id_val),
    )


def _fitting_row(e, doc):
    eid = e.Id.IntegerValue
    try:
        tname = safe_name(doc.GetElement(e.GetTypeId()))
    except Exception:
        tname = "?"
    conns = get_connectors(e)
    od = get_od(e, conns)
    if len(conns) < 2:
        return None
    sp = conns[0].Origin; ep = conns[1].Origin

    ctr, radius = arc_from_connectors(e)
    angle = get_bend_angle(e)
    br_param = get_param_mm(e, "Bend Radius")
    bend_r = None
    if radius:     bend_r = ft2mm(radius)
    elif br_param: bend_r = br_param
    bend_a = angle if angle and angle > 0.5 else 0.0

    return "{},{},{},{},{},{},{},{},{},{},{}".format(
        eid, tname,
        fmt(ft2mm(sp.X)), fmt(ft2mm(sp.Y)), fmt(ft2mm(sp.Z)),
        fmt(ft2mm(ep.X)), fmt(ft2mm(ep.Y)), fmt(ft2mm(ep.Z)),
        fmt(od), fmt(bend_r), fmt(bend_a),
    )


def export_pipework(doc, uidoc, output_folder, log=None):
    """Main entry point.

    Splits the current selection into pipes/conduits and fittings, writes
    pipes_<TS>.csv and fittings_<TS>.csv into `output_folder`.
    Returns a summary dict.
    """
    def _say(msg):
        if log is not None: log(msg)

    sel_ids = uidoc.Selection.GetElementIds()
    if not sel_ids:
        raise ValueError("Select pipes/conduits and fittings first.")

    pipes = []; fits = []
    for eid in sel_ids:
        e = doc.GetElement(eid)
        cat = e.Category
        if not cat:
            continue
        bi = cat.Id.IntegerValue
        if bi in PIPE_CATS:
            pipes.append(e)
        elif bi in FIT_CATS:
            fits.append(e)

    if not pipes and not fits:
        raise ValueError("No pipes, conduits, or fittings in selection.")

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    result = {"timestamp": ts, "folder": output_folder, "files": []}

    # Pipes CSV
    if pipes:
        header = "ID,Type,Start_X,Start_Y,Start_Z,End_X,End_Y,End_Z,Length_mm,OD_mm,ID_mm"
        rows = []
        for e in pipes:
            r = _pipe_row(e, doc)
            if r: rows.append(r)
        path = os.path.join(output_folder, "pipes_{}.csv".format(ts))
        write_csv(path, header, rows)
        result["pipe_count"] = len(rows)
        result["files"].append(path)
        _say("Pipes:     **{}** rows -> `pipes_{}.csv`".format(len(rows), ts))
    else:
        result["pipe_count"] = 0

    # Fittings CSV
    if fits:
        header = "ID,Type,Start_X,Start_Y,Start_Z,End_X,End_Y,End_Z,OD_mm,BendRadius_mm,BendAngle_deg"
        rows = []
        for e in fits:
            r = _fitting_row(e, doc)
            if r: rows.append(r)
        path = os.path.join(output_folder, "fittings_{}.csv".format(ts))
        write_csv(path, header, rows)
        result["fit_count"] = len(rows)
        result["files"].append(path)
        _say("Fittings:  **{}** rows -> `fittings_{}.csv`".format(len(rows), ts))
    else:
        result["fit_count"] = 0

    return result
