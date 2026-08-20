# -*- coding: utf-8 -*-
"""Path - roads and paving helpers. PURE PYTHON (no Revit imports)
so the CPython suite tests it: the kerb settings keys and the
ground-slope angle maths.

The Angled Kerb button lays kerb units end-to-end along a picked line,
draped onto the terrain like a fence: each piece sits at its bay's
CENTRE, rotated to the line's plan tangent (the chord of the
tessellated curve, so curved lines get the curve's tangent), and the
terrain's slope ALONG the line at that point is written to the
family's angle parameter as a value between -90 and +90 degrees -
positive climbs in the line's direction.

The Flat Kerb button lays the same units LEVEL: nothing is tilted, so
it has no angle parameter and no slope fit, and its family and unit
length are remembered separately.
"""

SETTINGS_KERB_FAMILY = "path_kerb_family"
SETTINGS_KERB_LENGTH = "path_kerb_length_mm"
SETTINGS_KERB_ANGLE_PARAM = "path_kerb_angle_param"
SETTINGS_KERB_LENGTH_PARAM = "path_kerb_length_param"
SETTINGS_KERB_SLOPE_FIT = "path_kerb_slope_fit"

# FLAT kerb - its own family and unit length: a flat-laid kerb is a
# different product from an angled one, so the two buttons never
# overwrite each other's pick
SETTINGS_FLAT_KERB_FAMILY = "path_flat_kerb_family"
SETTINGS_FLAT_KERB_LENGTH = "path_flat_kerb_length_mm"
SETTINGS_FLAT_KERB_LENGTH_PARAM = "path_flat_kerb_length_param"

KERB_ANGLE_PARAM = "Angle"           # default parameter name

# a kerb may come from these categories
KERB_CATEGORIES = ["OST_GenericModel", "OST_StructuralFraming",
                   "OST_Site"]

# pieces shorter than this are not worth a unit
KERB_MIN_MM = 50.0


def kerb_settings(settings):
    """(family label, piece length mm, angle parameter, length
    parameter, slope fit) - the Kerb dialog's remembered values."""
    try:
        ln = float(settings.get(SETTINGS_KERB_LENGTH) or 915.0)
    except Exception:
        ln = 915.0
    return (str(settings.get(SETTINGS_KERB_FAMILY) or ""),
            ln,
            str(settings.get(SETTINGS_KERB_ANGLE_PARAM) or
                KERB_ANGLE_PARAM).strip() or KERB_ANGLE_PARAM,
            str(settings.get(SETTINGS_KERB_LENGTH_PARAM)
                or "").strip(),
            bool(settings.get(SETTINGS_KERB_SLOPE_FIT, False)))


def flat_kerb_settings(settings):
    """(family label, piece length mm, length parameter) - the Flat
    Kerb dialog's remembered values. A flat unit is laid LEVEL, so it
    has no angle parameter and no slope fit; it steps with the ground
    instead of tilting onto it."""
    try:
        ln = float(settings.get(SETTINGS_FLAT_KERB_LENGTH) or 915.0)
        if ln <= 0:
            ln = 915.0
    except Exception:
        ln = 915.0
    return (str(settings.get(SETTINGS_FLAT_KERB_FAMILY) or ""),
            ln,
            str(settings.get(SETTINGS_FLAT_KERB_LENGTH_PARAM)
                or "").strip())


def slope_fit_advance(unit, dz):
    """SLOPE FIT: the PLAN advance of one unit lying ON the slope.
    The unit is the HYPOTENUSE, its rise over the step is ``dz`` -
    the horizontal leg is sqrt(unit^2 - dz^2) (Pythagoras), so the
    next unit starts exactly where this one ends and they TOUCH. A
    rise steeper than the unit itself caps the advance at a tenth
    of the unit, so the walk always moves forward."""
    import math
    d = unit * unit - dz * dz
    floor2 = (0.1 * unit) ** 2
    if d <= floor2:
        return 0.1 * unit
    return math.sqrt(d)


def slope_angle_deg(dz, run):
    """The ground's slope ALONG the travel direction as DEGREES in
    [-90, 90]: ``dz`` = rise over the horizontal ``run`` (same
    units). Positive climbs, negative falls; a vertical face caps at
    +/-90."""
    import math
    if run is None or run <= 1e-12:
        if dz > 0:
            return 90.0
        if dz < 0:
            return -90.0
        return 0.0
    a = math.degrees(math.atan2(dz, run))
    return max(-90.0, min(90.0, a))
