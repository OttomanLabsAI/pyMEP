# -*- coding: utf-8 -*-
"""Mesh maths - PURE PYTHON (no Revit imports) so the CPython suite
tests it: the vertical line-through-triangles lookup behind Drape
Floor's geometry fallback.

ReferenceIntersector can come back empty even when the terrain is
plainly there (linked toposolids, view state, API regressions), so
the draper falls back to reading the terrain's OWN triangles and
answering 'what is the ground Z under (x, y)?' with plain barycentric
maths - no view involved at all."""

EPS = 1e-9

# a point this close outside an edge still counts as inside, so floor
# boundaries that coincide with terrain edges don't fall through the
# crack between two triangles
EDGE_TOL = 1e-7


def tri_z_at(tri, x, y):
    """The triangle's surface Z over plan point (x, y), or None when
    the point is outside its plan footprint (or the triangle is a
    plan-degenerate sliver - a vertical face)."""
    (x1, y1, z1), (x2, y2, z2), (x3, y3, z3) = tri
    d = (y2 - y3) * (x1 - x3) + (x3 - x2) * (y1 - y3)
    if abs(d) < EPS:
        return None
    a = ((y2 - y3) * (x - x3) + (x3 - x2) * (y - y3)) / d
    b = ((y3 - y1) * (x - x3) + (x1 - x3) * (y - y3)) / d
    c = 1.0 - a - b
    if a < -EDGE_TOL or b < -EDGE_TOL or c < -EDGE_TOL:
        return None
    return a * z1 + b * z2 + c * z3


def surface_z(tris, x, y, lowest=False):
    """The TOPMOST (default) or LOWEST surface Z under plan point
    (x, y) across all triangles; None when nothing lies under it.
    tris are ((x,y,z), (x,y,z), (x,y,z)) tuples."""
    best = None
    for tri in tris:
        z = tri_z_at(tri, x, y)
        if z is None:
            continue
        if best is None or (z < best if lowest else z > best):
            best = z
    return best
