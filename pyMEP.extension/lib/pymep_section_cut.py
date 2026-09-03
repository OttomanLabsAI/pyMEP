# -*- coding: utf-8 -*-
"""Section-cut maths - PURE PYTHON (no Revit imports) so the CPython suite
tests it: does a section plane, bounded by its crop width and height,
actually CUT any pipework?

Create Chamber Sections builds one section per chamber side. A side whose
plane no pipe / conduit / duct crosses shows nothing but the empty vault
wall, so it is dropped and the surviving sides are lettered A, B, C... in
order. The test is the section's own crop frame:

  frame = (origin, right, up, look)   three-tuples in world feet
  local x = along `right` (across the view), local y = along `up`,
  local z = along `look` (positive = beyond the plane, into the view)

A run is cut when its centreline crosses the plane (local z = 0) inside the
crop rectangle |x| <= half_w, |y| <= half_h. The run's radius widens both
the plane band and the rectangle so a pipe ending ON the plane, or grazing
the crop edge, still counts as cut (keeping a section is the safe side of
a marginal call)."""

EPS = 1e-9

SIDE_LETTERS = ("A", "B", "C", "D")


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def to_local(frame, p):
    """World point -> (x, y, z) in the section's crop frame."""
    origin, right, up, look = frame
    d = _sub(p, origin)
    return (_dot(d, right), _dot(d, up), _dot(d, look))


def _in_rect(x, y, half_w, half_h, grow=0.0):
    return (abs(x) <= half_w + grow + EPS) and (abs(y) <= half_h + grow + EPS)


def _seg_hits_rect(x0, y0, x1, y1, half_w, half_h, grow=0.0):
    """2-D segment vs axis-aligned rectangle (Liang-Barsky clip)."""
    w = half_w + grow
    h = half_h + grow
    dx = x1 - x0
    dy = y1 - y0
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x0 + w), (dx, w - x0), (-dy, y0 + h), (dy, h - y0)):
        if abs(p) < EPS:
            if q < -EPS:
                return False
            continue
        r = q / p
        if p < 0:
            if r > t1:
                return False
            if r > t0:
                t0 = r
        else:
            if r < t0:
                return False
            if r < t1:
                t1 = r
    return t0 <= t1 + EPS


def segment_cut(frame, p0, p1, half_w, half_h, radius=0.0):
    """True when the run p0-p1 (world feet) is cut by the framed section."""
    radius = max(float(radius or 0.0), 0.0)
    x0, y0, z0 = to_local(frame, p0)
    x1, y1, z1 = to_local(frame, p1)
    tol = radius + EPS
    if z0 > tol and z1 > tol:
        return False          # wholly beyond the plane
    if z0 < -tol and z1 < -tol:
        return False          # wholly behind the plane
    dz = z1 - z0
    if abs(dz) < EPS or (abs(z0) <= tol and abs(z1) <= tol):
        # Lying in the plane band: cut wherever it overlaps the crop.
        return _seg_hits_rect(x0, y0, x1, y1, half_w, half_h, radius)
    t = z0 / (z0 - z1)
    if t < 0.0:
        t = 0.0
    elif t > 1.0:
        t = 1.0
    x = x0 + (x1 - x0) * t
    y = y0 + (y1 - y0) * t
    return _in_rect(x, y, half_w, half_h, radius)


def polyline_cut(frame, pts, half_w, half_h, radius=0.0):
    """segment_cut over consecutive points (flex runs, tessellated arcs)."""
    pts = list(pts or [])
    for i in range(len(pts) - 1):
        if segment_cut(frame, pts[i], pts[i + 1], half_w, half_h, radius):
            return True
    return False


def box_cut(frame, bmin, bmax, half_w, half_h):
    """Conservative test for a fitting's world AABB: True when the box
    straddles the plane and its footprint on the plane overlaps the crop
    rectangle (an over-estimate for boxes not aligned to the frame, which
    errs toward keeping the section)."""
    lo_z = hi_z = None
    lo_x = hi_x = lo_y = hi_y = None
    for x in (bmin[0], bmax[0]):
        for y in (bmin[1], bmax[1]):
            for z in (bmin[2], bmax[2]):
                lx, ly, lz = to_local(frame, (x, y, z))
                if lo_z is None or lz < lo_z:
                    lo_z = lz
                if hi_z is None or lz > hi_z:
                    hi_z = lz
                if lo_x is None or lx < lo_x:
                    lo_x = lx
                if hi_x is None or lx > hi_x:
                    hi_x = lx
                if lo_y is None or ly < lo_y:
                    lo_y = ly
                if hi_y is None or ly > hi_y:
                    hi_y = ly
    if lo_z is None or lo_z > EPS or hi_z < -EPS:
        return False
    if hi_x < -half_w - EPS or lo_x > half_w + EPS:
        return False
    if hi_y < -half_h - EPS or lo_y > half_h + EPS:
        return False
    return True


def plan_sides(cut_counts, letters=SIDE_LETTERS):
    """Decide which sides to build and what to call them.

    cut_counts: pipework hits per side, in side order.
    Returns (sides, all_kept) where sides is a list of
    (side_index, local_letter, final_letter) for the sides to create,
    lettered A, B, C... in side order. When NO side cuts anything there is
    nothing to choose between, so every side is kept under its own letter
    and all_kept is True."""
    counts = [int(c or 0) for c in cut_counts]
    kept = [i for i, c in enumerate(counts) if c > 0]
    if not kept:
        return ([(i, letters[i], letters[i]) for i in range(len(counts))],
                True)
    return ([(i, letters[i], letters[n]) for n, i in enumerate(kept)],
            False)


def letters_needed(plans, letters=SIDE_LETTERS):
    """Final letters any chamber in `plans` (lists from plan_sides) will
    use - what the per-side section-type prompt has to ask for."""
    most = 0
    for sides in plans:
        if len(sides) > most:
            most = len(sides)
    return tuple(letters[:most])
