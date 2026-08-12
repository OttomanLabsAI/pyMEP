# -*- coding: utf-8 -*-
"""Path - roads and paving helpers. PURE PYTHON (no Revit imports)
so the CPython suite tests it: the kerb settings keys and the
ground-slope angle maths.

The Kerb button lays kerb units end-to-end along a picked line,
draped onto the terrain like a fence: each piece sits at its bay's
CENTRE, rotated to the line's plan tangent (the chord of the
tessellated curve, so curved lines get the curve's tangent), and the
terrain's slope ALONG the line at that point is written to the
family's angle parameter as a value between -90 and +90 degrees -
positive climbs in the line's direction.
"""

SETTINGS_KERB_FAMILY = "path_kerb_family"
SETTINGS_KERB_LENGTH = "path_kerb_length_mm"
SETTINGS_KERB_ANGLE_PARAM = "path_kerb_angle_param"
SETTINGS_KERB_LENGTH_PARAM = "path_kerb_length_param"

KERB_ANGLE_PARAM = "Angle"           # default parameter name

# a kerb may come from these categories
KERB_CATEGORIES = ["OST_GenericModel", "OST_StructuralFraming",
                   "OST_Site"]

# pieces shorter than this are not worth a unit
KERB_MIN_MM = 50.0


def kerb_settings(settings):
    """(family label, piece length mm, angle parameter, length
    parameter) - the Kerb dialog's remembered values."""
    try:
        ln = float(settings.get(SETTINGS_KERB_LENGTH) or 915.0)
    except Exception:
        ln = 915.0
    return (str(settings.get(SETTINGS_KERB_FAMILY) or ""),
            ln,
            str(settings.get(SETTINGS_KERB_ANGLE_PARAM) or
                KERB_ANGLE_PARAM).strip() or KERB_ANGLE_PARAM,
            str(settings.get(SETTINGS_KERB_LENGTH_PARAM)
                or "").strip())


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
