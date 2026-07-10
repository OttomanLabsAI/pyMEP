# -*- coding: utf-8 -*-
"""Place rectangular Revit ducts from a duct_centrelines CSV.

Entry point:
    build_ducts_from_centrelines(doc, csv_path, duct_type_name,
                                 system_type_name, log=None) -> (created, failed)

The CSV is the ``duct_centrelines_<TS>.csv`` produced by run_analysis.py,
so we trust its schema - no detective work on unexpected inputs. One row
per straight (horizontal + sloped); Revit's Duct.Create handles sloped
endpoints natively so both kinds go through the same path.
"""

import clr
clr.AddReference("RevitAPI")
# No clr.AddReference for "RevitAPIMechanical" - no such DLL ships with
# current Revit; the Mechanical namespace lives in RevitAPI.dll.

from Autodesk.Revit.DB import (
    BuiltInParameter, FilteredElementCollector, Level, Transaction,
)
from Autodesk.Revit.DB.Mechanical import (
    Duct, DuctType, MechanicalSystemType,
)

from pymep_revit import safe_name, mm2ft, ft2mm, xyz_mm
from pymep_csv import read_csv_dicts


def _say(log, msg):
    if log is not None:
        log(msg)


def _find_by_name(doc, cls, name):
    for el in FilteredElementCollector(doc).OfClass(cls):
        try:
            if safe_name(el) == name:
                return el
        except Exception:
            continue
    return None


def build_ducts_from_centrelines(doc, csv_path,
                                 duct_type_name, system_type_name, log=None):
    """Place a duct per row in the CSV. Returns (created, failed)."""
    rows = read_csv_dicts(csv_path)
    if not rows:
        raise ValueError("CSV has no data rows.")

    duct_type = _find_by_name(doc, DuctType, duct_type_name)
    if duct_type is None:
        raise ValueError("DuctType '{}' not found in the active document."
                         .format(duct_type_name))

    sys_type = _find_by_name(doc, MechanicalSystemType, system_type_name)
    if sys_type is None:
        raise ValueError("MechanicalSystemType '{}' not found in the active "
                         "document.".format(system_type_name))

    levels = sorted(
        FilteredElementCollector(doc).OfClass(Level).ToElements(),
        key=lambda lv: lv.Elevation)
    if not levels:
        raise ValueError("No levels in the active document.")

    _say(log, "Parsed **{}** centreline rows.".format(len(rows)))
    _say(log, "Duct type:   **{}**".format(duct_type_name))
    _say(log, "System type: **{}**".format(system_type_name))

    created = 0
    failed  = 0
    with Transaction(doc, "Build Ducts From Centrelines") as t:
        t.Start()

        for ri, row in enumerate(rows):
            try:
                sx = float(row["StartX_mm"]); sy = float(row["StartY_mm"])
                sz = float(row["StartZ_mm"])
                ex = float(row["EndX_mm"]);   ey = float(row["EndY_mm"])
                ez = float(row["EndZ_mm"])
                width_mm  = float(row["Width_mm"])
                height_mm = float(row["Height_mm"])

                if width_mm  <= 0 or height_mm <= 0:
                    raise ValueError("non-positive W or H")

                start_pt = xyz_mm(sx, sy, sz)
                end_pt   = xyz_mm(ex, ey, ez)
                if start_pt.DistanceTo(end_pt) < 1e-6:
                    raise ValueError("coincident start/end")

                z_mid_ft = mm2ft((sz + ez) / 2.0)
                level = min(levels, key=lambda lv: abs(lv.Elevation - z_mid_ft))

                duct = Duct.Create(doc, sys_type.Id, duct_type.Id, level.Id,
                                   start_pt, end_pt)

                w_param = duct.get_Parameter(BuiltInParameter.RBS_CURVE_WIDTH_PARAM)
                h_param = duct.get_Parameter(BuiltInParameter.RBS_CURVE_HEIGHT_PARAM)
                if w_param is None or h_param is None:
                    raise ValueError("duct type '{}' has no W/H params "
                                     "(is it rectangular?)".format(duct_type_name))

                # If this duct type exposes a "Size Lock", clear it so our
                # explicit width/height aren't resisted. (Visible as the
                # "Size Lock" checkbox in the duct's Properties.)
                lock = duct.LookupParameter("Size Lock")
                if lock is not None and not lock.IsReadOnly:
                    try:
                        lock.Set(0)
                    except Exception:
                        pass

                w_ft = mm2ft(width_mm)
                h_ft = mm2ft(height_mm)

                # IMPORTANT (two separate Revit gotchas, both fixed here):
                #
                # 1) Regeneration: Revit DROPS the first of two size edits when
                #    both are set in one transaction with no regeneration
                #    between them (The Building Coder, "Setting Duct Width and
                #    Height Requires Regeneration"). The old code set width then
                #    height back-to-back, so the width edit was lost and the
                #    duct kept the type's seed size - e.g. a 330x330 request
                #    came out ~90 wide. Fix: regenerate after EACH set.
                #
                # 2) Orientation swap: which physical dimension Revit calls
                #    "Width" vs "Height" depends on the duct's run direction.
                #    For a square encasement (W == H) this is irrelevant. For a
                #    rectangular one we verify and, if needed, set them swapped.
                w_param.Set(w_ft)
                doc.Regenerate()
                h_param.Set(h_ft)
                doc.Regenerate()

                def _live():
                    return (duct.get_Parameter(BuiltInParameter.RBS_CURVE_WIDTH_PARAM).AsDouble(),
                            duct.get_Parameter(BuiltInParameter.RBS_CURVE_HEIGHT_PARAM).AsDouble())

                tol_ft = mm2ft(0.5)  # 0.5 mm tolerance
                w_now, h_now = _live()
                if abs(w_now - w_ft) > tol_ft or abs(h_now - h_ft) > tol_ft:
                    # Re-assert each value with a regenerate between; handles the
                    # orientation swap and any stale mid-regeneration read.
                    duct.get_Parameter(BuiltInParameter.RBS_CURVE_HEIGHT_PARAM).Set(h_ft)
                    doc.Regenerate()
                    duct.get_Parameter(BuiltInParameter.RBS_CURVE_WIDTH_PARAM).Set(w_ft)
                    doc.Regenerate()
                    w_now, h_now = _live()

                # Accept either orientation: the SET {W, H} must match what we
                # asked for. (For the square single-pipe case the two are
                # identical, so this is just an exactness check.)
                got  = sorted((round(ft2mm(w_now), 1), round(ft2mm(h_now), 1)))
                want = sorted((round(width_mm, 1), round(height_mm, 1)))
                if got != want:
                    raise ValueError(
                        "duct size did not take: wanted {:.0f} x {:.0f} mm, "
                        "got {:.1f} x {:.1f} mm".format(
                            width_mm, height_mm,
                            ft2mm(w_now), ft2mm(h_now)))

                # Mark = "C{collection}-O{order}", e.g. C1-O3. Duct.Mark is
                # the instance "Mark" parameter (RBS_MARK on MEP elements,
                # ALL_MODEL_MARK as a fallback on some Revit versions).
                col_str   = (row.get("Collection") or "").strip()
                order_str = (row.get("Order")      or "").strip()
                mark_str  = "C{}-O{}".format(col_str, order_str)
                mark_param = (duct.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)
                              or duct.LookupParameter("Mark"))
                if mark_param is not None and not mark_param.IsReadOnly:
                    mark_param.Set(mark_str)

                created += 1
                _say(log, "  Row {}: {} | {:.0f} x {:.0f} mm [OK]"
                          .format(ri + 1, mark_str, width_mm, height_mm))

            except Exception as ex:
                failed += 1
                _say(log, "  Row {}: FAILED - {}".format(ri + 1, ex))

        t.Commit()

    return created, failed
