# -*- coding: utf-8 -*-
"""Create Chamber Sections dialog helpers - PURE PYTHON (no Revit or WPF
imports) so the CPython suite tests them: the remembered settings behind
the dialog, the mm field parser and the family-type search filter.

The dialog itself (pymep_chamber_sections.xaml) is driven from the
Create Sections button; everything here is what it reads and writes.

Also home to the chamber NAMING rule shared by Chamber Plans, Create
Sections, Match Sections and Sheet Setup: a chamber's scope box, plan and
sections are named after its KEY - the Mark up to the first slash. The
"/Z1" style tail is a zone guide, not identity, and never reaches a name."""


def chamber_key(mark):
    """'LV1' for a Mark 'LV1/Z1'; a Mark without a slash is its own key;
    blank or None -> empty string."""
    if not mark:
        return u""
    text = u"{0}".format(mark).strip()
    head = text.split(u"/", 1)[0].strip()
    return head or text

SETTINGS_SECTION_OFFSET = "chamber_section_offset_mm"
SETTINGS_SECTION_HEIGHT = "chamber_section_height_mm"
SETTINGS_SECTION_DEPTH = "chamber_section_depth_mm"
SETTINGS_SECTION_TYPE = "chamber_section_type"
SETTINGS_SECTION_SIDE_TYPES = "chamber_section_side_types"
SETTINGS_SECTION_SAME_TYPE = "chamber_section_same_type"
SETTINGS_SECTION_CUT_ONLY = "chamber_section_cut_only"

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
