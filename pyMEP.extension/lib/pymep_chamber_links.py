# -*- coding: utf-8 -*-
"""Shared helpers for Associate / Update Chamber Sections buttons.

Stores, per project, the relative placement of each chamber section view with
respect to its chamber family instance: the section origin expressed in the
chamber's LOCAL (rotation-corrected) frame, plus the section's own rotation
relative to the chamber. This lets Update re-place sections after a chamber is
moved or rotated.

JSON lives at <ext root>/exports/<model>/chamber_section_links.json and is keyed
by section ElementId (as a string). Each record also stores the chamber Mark and
ElementId so the chamber can be re-found even if its ElementId changes.

IronPython 2.7: pure ASCII, no f-strings, LF endings.
"""

import os
import json
import math

from Autodesk.Revit import DB

MM_PER_FOOT = 304.8
LINK_FILENAME = "chamber_section_links.json"


# ---------------------------------------------------------------------------
# JSON path + load/save
# ---------------------------------------------------------------------------
def _safe_name(text):
    keep = []
    for ch in text:
        if ch.isalnum() or ch in (" ", "_", "-", "."):
            keep.append(ch)
        else:
            keep.append("_")
    return "".join(keep).strip() or "model"


def links_path(doc):
    # <ext root>/exports/<model>/chamber_section_links.json
    # This file lives at <ext root>/lib/, so ext root is one level up.
    lib_dir = os.path.dirname(os.path.abspath(__file__))
    ext_root = os.path.dirname(lib_dir)
    folder = os.path.join(ext_root, "exports", _safe_name(doc.Title))
    if not os.path.isdir(folder):
        try:
            os.makedirs(folder)
        except Exception:
            pass
    return os.path.join(folder, LINK_FILENAME)


class LinksReadError(Exception):
    """Raised when the links file EXISTS but cannot be read or parsed.

    Callers must NOT save over the file in this case - doing so would wipe
    every stored association just because the file was locked, corrupt or an
    unsynced OneDrive placeholder.
    """
    pass


class LinksWriteError(Exception):
    """Raised when the temp-file swap in save_links fails.

    The message names the surviving .tmp path so callers can tell the user
    'associations not saved - recover from <path>.tmp'.
    """
    pass


def load_links(doc):
    # File absent -> empty dict (nothing stored yet, safe to save fresh).
    # File present but unreadable/unparseable -> LinksReadError (do NOT save).
    path = links_path(doc)
    if not os.path.exists(path):
        return {}
    try:
        f = open(path, "r")
        try:
            data = json.load(f)
        finally:
            f.close()
    except Exception as ex:
        raise LinksReadError(
            "Links file exists but could not be read/parsed: {0} ({1})".format(
                path, ex))
    if not isinstance(data, dict):
        raise LinksReadError(
            "Links file does not contain a JSON object: {0}".format(path))
    return data


def save_links(doc, data):
    # Write to a temp file first, then swap it in, so a crash mid-write can
    # never leave a truncated links file behind.
    path = links_path(doc)
    tmp = path + ".tmp"
    f = open(tmp, "w")
    try:
        json.dump(data, f, indent=2, sort_keys=True)
    finally:
        f.close()
    # py2/Windows os.rename fails if the destination exists - remove it first.
    # The remove -> rename swap is guarded: if the rename fails AFTER the live
    # file was removed, try to restore the live file from the temp copy (which
    # holds the full merged data) so nothing is lost, then raise a
    # LinksWriteError naming the surviving .tmp file for manual recovery.
    try:
        if os.path.exists(path):
            os.remove(path)
        os.rename(tmp, path)
    except Exception as ex:
        restored = False
        if not os.path.exists(path):
            try:
                src = open(tmp, "r")
                try:
                    content = src.read()
                finally:
                    src.close()
                dst = open(path, "w")
                try:
                    dst.write(content)
                finally:
                    dst.close()
                restored = True
            except Exception:
                restored = False
        note = ("the live file was restored from the temp copy"
                if restored else "the live file could NOT be restored")
        raise LinksWriteError(
            "Failed to swap the links file into place: {0} ({1}); {2}. "
            "Associations not saved - recover from {3}".format(
                path, ex, note, tmp))
    return path


# ---------------------------------------------------------------------------
# Geometry: chamber frame + section pose
# ---------------------------------------------------------------------------
def chamber_pose(inst):
    # Returns (origin_xyz, angle_rad) for a point-based family instance.
    # angle is rotation about Z (project Z), 0 for an unrotated instance.
    loc = inst.Location
    if not isinstance(loc, DB.LocationPoint):
        return None
    pt = loc.Point
    ang = 0.0
    try:
        ang = loc.Rotation
    except Exception:
        ang = 0.0
    return pt, ang


def section_angle(view):
    # In-plan rotation of a section, from its RightDirection projected to XY.
    try:
        rd = view.RightDirection
        return math.atan2(rd.Y, rd.X)
    except Exception:
        return 0.0


def section_origin(view):
    # The section's world origin. Prefer the CropBox transform origin (this is
    # what actually places the section); fall back to view.Origin.
    try:
        bb = view.CropBox
        if bb is not None and bb.Transform is not None:
            return bb.Transform.Origin
    except Exception:
        pass
    return view.Origin


def section_angle_from_crop(view):
    # In-plan rotation from the CropBox transform BasisX (matches RightDirection).
    try:
        bb = view.CropBox
        if bb is not None and bb.Transform is not None:
            bx = bb.Transform.BasisX
            return math.atan2(bx.Y, bx.X)
    except Exception:
        pass
    return section_angle(view)


def set_section_pose(view, target_origin, target_angle):
    # Reposition + reorient a section, MEASURING what actually happened.
    # Returns (ok, message, achieved_dict) where achieved_dict has:
    #   before_origin, after_origin, moved_mm, before_angle, after_angle,
    #   rotated_deg, method.
    #
    # Section views are awkward: depending on Revit version, translation works
    # via ElementTransformUtils.MoveElement(view.Id, ...) and rotation via
    # RotateElement(view.Id, axis, angle). We try those, then re-read the
    # section origin/angle to confirm. CropBox-origin writes are unreliable for
    # MOVING (often only resize the crop) so they are a last-resort fallback.
    import math as _m

    doc = view.Document

    def _origin():
        try:
            bb = view.CropBox
            if bb is not None and bb.Transform is not None:
                return bb.Transform.Origin
        except Exception:
            pass
        return view.Origin

    def _angle():
        try:
            rd = view.RightDirection
            return _m.atan2(rd.Y, rd.X)
        except Exception:
            return 0.0

    before_o = _origin()
    before_a = _angle()
    method_used = []

    # --- ROTATION via RotateElement about vertical axis through current origin
    dangle = target_angle - before_a
    while dangle > _m.pi:
        dangle -= 2.0 * _m.pi
    while dangle < -_m.pi:
        dangle += 2.0 * _m.pi
    if abs(dangle) > 1.0e-6:
        try:
            o = _origin()
            axis = DB.Line.CreateBound(o, DB.XYZ(o.X, o.Y, o.Z + 1.0))
            DB.ElementTransformUtils.RotateElement(doc, view.Id, axis, dangle)
            method_used.append("RotateElement")
        except Exception as ex:
            method_used.append("RotateElement-failed:%s" % ex)

    # --- TRANSLATION via MoveElement from (post-rotation) origin to target
    mid_o = _origin()
    tx = target_origin.X - mid_o.X
    ty = target_origin.Y - mid_o.Y
    tz = target_origin.Z - mid_o.Z
    if (tx * tx + ty * ty + tz * tz) ** 0.5 > 1.0e-4:
        try:
            DB.ElementTransformUtils.MoveElement(doc, view.Id, DB.XYZ(tx, ty, tz))
            method_used.append("MoveElement")
        except Exception as ex:
            method_used.append("MoveElement-failed:%s" % ex)

    # --- Measure what we actually achieved
    after_o = _origin()
    after_a = _angle()
    moved = ((after_o.X - before_o.X) ** 2 +
             (after_o.Y - before_o.Y) ** 2 +
             (after_o.Z - before_o.Z) ** 2) ** 0.5
    rotated = _m.degrees(after_a - before_a)
    while rotated > 180.0:
        rotated -= 360.0
    while rotated < -180.0:
        rotated += 360.0

    # Did we land near the target?
    miss = ((after_o.X - target_origin.X) ** 2 +
            (after_o.Y - target_origin.Y) ** 2 +
            (after_o.Z - target_origin.Z) ** 2) ** 0.5

    achieved = {
        "before_origin": before_o,
        "after_origin": after_o,
        "moved_mm": moved * MM_PER_FOOT,
        "miss_mm": miss * MM_PER_FOOT,
        "before_angle_deg": _m.degrees(before_a),
        "after_angle_deg": _m.degrees(after_a),
        "rotated_deg": rotated,
        "method": ", ".join(method_used) if method_used else "none-needed",
    }

    # Success = we ended up within 1 mm of target (or nothing needed doing).
    needed_move = (tx * tx + ty * ty + tz * tz) ** 0.5 > 1.0e-4
    needed_rot = abs(dangle) > 1.0e-6
    if not needed_move and not needed_rot:
        return True, "already in place", achieved
    if miss * MM_PER_FOOT <= 1.0:
        return True, None, achieved
    return False, "did not reach target (off by %.0f mm)" % (miss * MM_PER_FOOT), achieved



def world_to_local(chamber_origin, chamber_angle, world_pt):
    # Express world_pt in the chamber's local frame: R(-theta) * (P - C).
    dx = world_pt.X - chamber_origin.X
    dy = world_pt.Y - chamber_origin.Y
    dz = world_pt.Z - chamber_origin.Z
    ca = math.cos(-chamber_angle)
    sa = math.sin(-chamber_angle)
    lx = dx * ca - dy * sa
    ly = dx * sa + dy * ca
    return (lx, ly, dz)


def local_to_world(chamber_origin, chamber_angle, local_xyz):
    # Inverse of world_to_local: C + R(theta) * local.
    lx, ly, lz = local_xyz
    ca = math.cos(chamber_angle)
    sa = math.sin(chamber_angle)
    wx = chamber_origin.X + (lx * ca - ly * sa)
    wy = chamber_origin.Y + (lx * sa + ly * ca)
    wz = chamber_origin.Z + lz
    return DB.XYZ(wx, wy, wz)


def make_record(view, chamber_inst, chamber_mark):
    # Build a JSON-serialisable record of the section's pose relative to chamber.
    # Use the CropBox transform as the source of the section's origin/angle so
    # that what we store matches what set_section_pose writes back.
    pose = chamber_pose(chamber_inst)
    if pose is None:
        return None
    c_origin, c_angle = pose
    s_origin = section_origin(view)
    s_angle = section_angle_from_crop(view)

    local = world_to_local(c_origin, c_angle, s_origin)
    rel_angle = s_angle - c_angle

    rec = {
        "section_name": view.Name,
        "chamber_mark": chamber_mark if chamber_mark else "",
        "chamber_eid": chamber_inst.Id.IntegerValue,
        "local_offset_ft": [local[0], local[1], local[2]],
        "rel_angle_rad": rel_angle,
        # Stored for reference/debugging (mm, degrees).
        "local_offset_mm": [local[0] * MM_PER_FOOT,
                            local[1] * MM_PER_FOOT,
                            local[2] * MM_PER_FOOT],
        "rel_angle_deg": math.degrees(rel_angle),
    }
    return rec


def target_pose_from_record(rec, chamber_inst):
    # Given a stored record and the (current) chamber, compute the section's
    # desired world origin and world angle.
    pose = chamber_pose(chamber_inst)
    if pose is None:
        return None
    c_origin, c_angle = pose
    local = rec.get("local_offset_ft")
    if not local or len(local) != 3:
        return None
    target_origin = local_to_world(c_origin, c_angle, local)
    target_angle = c_angle + float(rec.get("rel_angle_rad", 0.0))
    return target_origin, target_angle
