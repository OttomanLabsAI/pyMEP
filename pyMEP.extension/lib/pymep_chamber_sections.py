# -*- coding: utf-8 -*-
"""Create Chamber Sections dialog helpers - PURE PYTHON (no Revit or WPF
imports) so the CPython suite tests them: the remembered settings behind
the dialog, the mm field parser and the family-type search filter.

The dialog itself (pymep_chamber_sections.xaml) is driven from the
Create Sections button; everything here is what it reads and writes.

Also home to the chamber NAMING rule shared by Chamber Plans, Create
Sections, Match Sections and Sheet Setup: a chamber's scope box, plan and
sections are named after its KEY, which is the WHOLE Mark, trimmed -
"LV1/Z1" stays "LV1/Z1". The zone part matters: LV numbers repeat across
zones, so the Mark before the slash alone would collide."""


def chamber_key(mark):
    """The naming key of a Mark: the whole Mark, trimmed ('LV1/Z1' ->
    'LV1/Z1'); blank or None -> empty string."""
    if not mark:
        return u""
    return u"{0}".format(mark).strip()

SETTINGS_SECTION_OFFSET = "chamber_section_offset_mm"
SETTINGS_SECTION_HEIGHT = "chamber_section_height_mm"
SETTINGS_SECTION_DEPTH = "chamber_section_depth_mm"
SETTINGS_SECTION_TYPE = "chamber_section_type"
SETTINGS_SECTION_SIDE_TYPES = "chamber_section_side_types"
SETTINGS_SECTION_SAME_TYPE = "chamber_section_same_type"
SETTINGS_SECTION_CUT_ONLY = "chamber_section_cut_only"

# Chamber Plans dialog
SETTINGS_PLANS_TEMPLATE = "chamber_plans_template"
SETTINGS_PLANS_SEED = "chamber_plans_seed"
PLANS_TEMPLATE_ACTIVE = u"(same as the active view)"
PLANS_TEMPLATE_NONE = u"(no template)"
SEED_PREFERRED_NAME = u"sample_scope_box"

DEFAULT_OFFSET_MM = 1500.0
DEFAULT_HEIGHT_MM = 3000.0
DEFAULT_DEPTH_MM = 3000.0


def _mm(value, default):
    # None-safe: float(None) is a CLR SystemError under IronPython, so
    # never hand None to float().
    if value is None:
        return default
    try:
        v = float(value)
    except Exception:
        return default
    if v != v or v - v != 0 or v <= 0:      # nan, inf, non-positive
        return default
    return v


def section_settings(settings):
    """The dialog's remembered values as a dict:
    offset / height / depth (mm), type (section type name for 'same type
    for every side'), side_types ({letter: type name}), same (bool),
    cut_only (bool). Missing or broken entries fall back to defaults."""
    settings = settings or {}
    raw_sides = settings.get(SETTINGS_SECTION_SIDE_TYPES)
    side_types = {}
    if isinstance(raw_sides, dict):
        for k, v in raw_sides.items():
            if k and v:
                side_types[u"{0}".format(k).strip().upper()] = v
    return {
        "offset": _mm(settings.get(SETTINGS_SECTION_OFFSET),
                      DEFAULT_OFFSET_MM),
        "height": _mm(settings.get(SETTINGS_SECTION_HEIGHT),
                      DEFAULT_HEIGHT_MM),
        "depth": _mm(settings.get(SETTINGS_SECTION_DEPTH),
                     DEFAULT_DEPTH_MM),
        "type": settings.get(SETTINGS_SECTION_TYPE) or u"",
        "side_types": side_types,
        "same": bool(settings.get(SETTINGS_SECTION_SAME_TYPE, True)),
        "cut_only": bool(settings.get(SETTINGS_SECTION_CUT_ONLY, True)),
    }


def plans_settings(settings):
    """The Chamber Plans dialog's remembered values: template (the
    dropdown label - PLANS_TEMPLATE_ACTIVE, PLANS_TEMPLATE_NONE or a
    template name) and seed (scope box name, '' for the default pick)."""
    settings = settings or {}
    tmpl = settings.get(SETTINGS_PLANS_TEMPLATE) or PLANS_TEMPLATE_ACTIVE
    return {
        "template": u"{0}".format(tmpl),
        "seed": u"{0}".format(settings.get(SETTINGS_PLANS_SEED) or u""),
    }


def pick_seed_name(names, remembered=u""):
    """Which scope box the Chamber Plans dialog should offer first: the
    remembered one if it still exists, else 'sample_scope_box' (any case),
    else the only box, else None (the user picks)."""
    names = list(names or [])
    if remembered and remembered in names:
        return remembered
    for n in names:
        if u"{0}".format(n).strip().lower() == SEED_PREFERRED_NAME:
            return n
    if len(names) == 1:
        return names[0]
    return None


def parse_mm(text):
    """A positive mm value typed into a field, or None. Tolerates a
    trailing 'mm', spaces and a decimal comma."""
    if text is None:
        return None
    try:
        s = text.strip()
    except Exception:
        return None
    if s.lower().endswith("mm"):
        s = s[:-2].strip()
    s = s.replace(",", ".")
    if not s:
        return None
    try:
        v = float(s)
    except Exception:
        return None
    if v != v or v - v != 0 or v <= 0:      # nan, inf, non-positive
        return None
    return v


def mm_text(value):
    """A mm value as field text: 1500.0 -> '1500', 1234.5 -> '1234.5'."""
    try:
        return "{0:g}".format(float(value))
    except Exception:
        return ""


def filter_labels(labels, query):
    """Indexes of the labels matching a search box: every whitespace-
    separated word of the query must appear (case-insensitive). An empty
    query keeps everything."""
    words = [w for w in (query or u"").lower().split() if w]
    keep = []
    for i, label in enumerate(labels):
        low = (label or u"").lower()
        ok = True
        for w in words:
            if w not in low:
                ok = False
                break
        if ok:
            keep.append(i)
    return keep


# ---------------------------------------------------------------------------
# Scope box rotation: which chamber face goes to the top of the plan
# ---------------------------------------------------------------------------
import math as _math

RIGHT_ANGLE = _math.pi / 2.0


def wrap_angle(a, period):
    """`a` folded into (-period/2, period/2]."""
    half = period / 2.0
    a = _math.fmod(a, period)
    if a <= -half:
        a += period
    elif a > half:
        a -= period
    return a


def box_bottom(chamber_bottom, box_height, cut_plane, chamber_margin,
               cut_margin):
    """Where a scope box of a fixed height should START (its bottom Z) so
    it wraps the chamber from chamber_bottom - chamber_margin upward while
    the plan's cut plane still passes through it. Revit only shows a scope
    box in a plan - and only lets the plan take it - when the cut plane
    intersects the box, and the API cannot resize a box, only move it.

    Returns (bottom, reaches): reaches is False when the box is too short
    to hold both the chamber and the cut plane, in which case it sits as
    low as the cut plane allows (top = cut_plane + cut_margin)."""
    want = chamber_bottom - chamber_margin
    lowest_ok = cut_plane + cut_margin - box_height    # top clears the plane
    highest_ok = cut_plane - cut_margin                # bottom stays under it
    if want >= lowest_ok:
        return min(want, highest_ok), True
    return lowest_ok, False


def upright_rotation(chamber_angle, up_reference=0.0):
    """The rotation to give a chamber's scope box so that the chamber face
    most aligned with 'up' sits at the top of the plan.

    chamber_angle: the instance's plan rotation (radians, anticlockwise
    from project X). up_reference: the rotation of the direction that
    counts as 'up' relative to project north - 0 for a Project North plan,
    the project's angle to True North for a True North plan.

    A rotated scope box turns its plan view with it, so of the four
    rotations that align the box to the chamber (the angle plus any
    quarter turn) the one within 45 degrees of the up reference is
    chosen: the view stays as close to north-up as the chamber allows."""
    return up_reference + wrap_angle(chamber_angle - up_reference,
                                     RIGHT_ANGLE)
