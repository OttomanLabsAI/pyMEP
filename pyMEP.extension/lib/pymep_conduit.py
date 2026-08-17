# -*- coding: utf-8 -*-
"""Conduits - pure helpers (no Revit imports) so the CPython suite
tests them: the size-matching maths behind Pipes to Conduits.

The button turns each selected PIPE into a CONDUIT on the same line
with the same nominal diameter. Conduit diameters are not free values
- Revit only accepts nominals that exist in the conduit type's
STANDARD (Electrical Settings > Conduit Settings > Sizes), so the
button first adds any missing pipe sizes to that standard (the same
automation Place Pipes runs on the pipe segment) and only snaps to
the nearest existing size when the standard cannot be extended.
"""

SETTINGS_CONDUIT_TYPE = "conduit_type_name"

# a size within this of an available nominal counts as that size
SIZE_TOL_MM = 0.1


def nearest_size(available_mm, want_mm):
    """The closest available nominal to ``want_mm``; None when the
    list is empty. Ties go to the first listed."""
    best = None
    for a in available_mm:
        if best is None or abs(a - want_mm) < abs(best - want_mm):
            best = a
    return best


def missing_sizes(available_mm, wanted_mm, tol_mm=SIZE_TOL_MM):
    """The distinct wanted nominals with NO available size within
    ``tol_mm`` - the sizes the standard must gain before the conduits
    can take them. Sorted ascending, duplicates collapsed."""
    out = []
    for w in sorted(set(wanted_mm)):
        hit = False
        for a in available_mm:
            if abs(a - w) <= tol_mm:
                hit = True
                break
        if not hit:
            out.append(w)
    return out


def pick_size(available_mm, want_mm, tol_mm=SIZE_TOL_MM):
    """(size_to_use, exact) - the available nominal a conduit should
    take for a pipe of ``want_mm``: the within-tolerance match when
    one exists (exact=True), else the nearest available (exact=False,
    the caller reports the snap). (None, False) when nothing is
    available."""
    for a in available_mm:
        if abs(a - want_mm) <= tol_mm:
            return a, True
    return nearest_size(available_mm, want_mm), False
