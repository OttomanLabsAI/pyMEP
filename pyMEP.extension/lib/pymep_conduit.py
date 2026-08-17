# -*- coding: utf-8 -*-
"""Conduits - pure helpers (no Revit imports) so the CPython suite
tests them: the size-matching maths behind Pipes to Conduits and the
dialog's remembered settings.

The button turns each selected PIPE into a CONDUIT on the same line
with the same nominal diameter. Conduit diameters are not free values
- Revit only accepts nominals that exist in the conduit type's
STANDARD (Electrical Settings > Conduit Settings > Sizes), so the
dialog offers to CREATE the missing sizes on the standard the picked
type follows: the TRADE SIZE and the OUTER diameter are both the
pipe's size, and the INNER diameter is trade minus twice the conduit
thickness entered in the dialog. Only when size creation is off (or
the standard refuses) does a conduit snap to the nearest existing
size.
"""

SETTINGS_CONDUIT_TYPE = "conduit_type_name"
SETTINGS_CONDUIT_ADD_SIZES = "conduit_add_sizes"
SETTINGS_CONDUIT_WALL = "conduit_wall_mm"

# a size within this of an available nominal counts as that size
SIZE_TOL_MM = 0.1

DEFAULT_WALL_MM = 2.0


def conduit_settings(settings):
    """(type name, create missing sizes, conduit thickness mm) - the
    Pipes to Conduits dialog's remembered values."""
    try:
        wall = float(settings.get(SETTINGS_CONDUIT_WALL)
                     or DEFAULT_WALL_MM)
        if wall <= 0:
            wall = DEFAULT_WALL_MM
    except Exception:
        wall = DEFAULT_WALL_MM
    return (str(settings.get(SETTINGS_CONDUIT_TYPE) or ""),
            bool(settings.get(SETTINGS_CONDUIT_ADD_SIZES, True)),
            wall)


def match_standard(names, want):
    """The conduit-size-settings standard KEY that a type's reported
    Standard text means: exact match first, then case-insensitive and
    whitespace-tolerant. None when nothing matches (the dialog then
    leaves the pick to the user)."""
    if want is None:
        return None
    w = str(want).strip()
    if not w:
        return None
    for n in names:
        if n == w:
            return n
    wl = w.lower()
    for n in names:
        if str(n).strip().lower() == wl:
            return n
    return None


def inner_from_trade(trade_mm, wall_mm):
    """The INNER diameter of a created conduit size: the trade size
    (= outer diameter) minus the wall thickness on BOTH sides. Floored
    at a tenth of the trade so an over-thick wall can never collapse
    or invert the size."""
    inner = trade_mm - 2.0 * wall_mm
    floor_v = 0.1 * trade_mm
    if inner < floor_v:
        return floor_v
    return inner


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
