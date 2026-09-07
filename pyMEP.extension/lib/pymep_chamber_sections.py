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

import re


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

# Sizing from the chamber - shared by Create Sections and Chamber Plans.
# Either fixed numbers, or the chamber's own dimension parameters plus a
# clearance each side.
SIZE_FIXED = "fixed"
SIZE_PARAMS = "params"
SETTINGS_SIZE_MODE = "chamber_size_mode"
SETTINGS_SIZE_PARAM_X = "chamber_size_param_x"    # plan dim along local X
SETTINGS_SIZE_PARAM_Y = "chamber_size_param_y"    # plan dim along local Y
SETTINGS_SIZE_PARAM_H = "chamber_size_param_h"    # height
SETTINGS_SIZE_CLEAR = "chamber_size_clear_mm"     # clearance each side
DEFAULT_SIZE_PARAM_X = u"Width"
DEFAULT_SIZE_PARAM_Y = u"Length"
DEFAULT_SIZE_PARAM_H = u"Height"
DEFAULT_SIZE_CLEAR_MM = 500.0

# Sheets Full Pipeline dialog
SETTINGS_PIPE_NUMBER = "pipeline_sheet_number"
SETTINGS_PIPE_NAME = "pipeline_sheet_name"
SETTINGS_PIPE_START = "pipeline_sheet_start"
SETTINGS_PIPE_PER_SHEET = "pipeline_per_sheet"
SETTINGS_PIPE_TITLEBLOCK = "pipeline_titleblock"
SETTINGS_PIPE_DIMS = "pipeline_dims"
DEFAULT_PIPE_NUMBER = u"P{n}"
DEFAULT_PIPE_NAME = u"CHAMBERS SHEET {n}"
DEFAULT_PIPE_PER_SHEET = 2

# Dimension Section dialog
SETTINGS_DIM_TYPE = "dimension_section_dim_type"
SETTINGS_DIM_Z_PLANES = "dimension_section_z_planes"
SETTINGS_DIM_Z_MODE = "dimension_section_z_mode"    # Z_CHAIN | Z_DIRECT
SETTINGS_DIM_Z_SKIP = "dimension_section_z_skip"    # skip planes a view can't take
SETTINGS_DIM_RULES = "dimension_section_rules"      # list of plane-string rules
SETTINGS_DIM_SETS = "dimension_section_sets"        # {name: {"rules": [...]}} saved sets
DIM_AXES = ("z", "x", "y")
RULE_PLANES = "planes"      # rule kind: reference-plane string
RULE_PIPES = "pipes"        # rule kind: pipe / conduit / duct centreline strings
PIPE_CATS = ("pipe", "conduit", "duct")
Z_CHAIN = "chain"
Z_DIRECT = "direct"
DEFAULT_DIM_TYPE_NAME = u"RHD_2.5"

# Chamber Plans dialog
SETTINGS_PLANS_TEMPLATE = "chamber_plans_template"
SETTINGS_PLANS_SEED = "chamber_plans_seed"
SETTINGS_PLANS_EXTENTS = "chamber_plans_extents"  # EXTENTS_SCOPE | _CROP
SETTINGS_PLANS_WIDTH = "chamber_plans_width_mm"   # fixed crop, local X
SETTINGS_PLANS_DEPTH = "chamber_plans_depth_mm"   # fixed crop, local Y
SETTINGS_PLANS_WORKSET = "chamber_plans_workset"  # scope box workset name
CURRENT_WORKSET = u"(current workset)"
EXTENTS_SCOPE = "scope"
EXTENTS_CROP = "crop"
DEFAULT_PLANS_WIDTH_MM = 3000.0
DEFAULT_PLANS_DEPTH_MM = 3000.0
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


def pipeline_settings(settings):
    """The Sheets Full Pipeline dialog's remembered values: number and
    name patterns (with {n}), start number, chambers per sheet, title
    block label and whether to dimension."""
    settings = settings or {}
    try:
        start = int(settings.get(SETTINGS_PIPE_START) or 1)
    except Exception:
        start = 1
    try:
        per = int(settings.get(SETTINGS_PIPE_PER_SHEET)
                  or DEFAULT_PIPE_PER_SHEET)
    except Exception:
        per = DEFAULT_PIPE_PER_SHEET
    return {
        "number": u"{0}".format(settings.get(SETTINGS_PIPE_NUMBER)
                                or DEFAULT_PIPE_NUMBER),
        "name": u"{0}".format(settings.get(SETTINGS_PIPE_NAME)
                              or DEFAULT_PIPE_NAME),
        "start": start if start >= 0 else 1,
        "per_sheet": per if per >= 1 else DEFAULT_PIPE_PER_SHEET,
        "titleblock": u"{0}".format(settings.get(SETTINGS_PIPE_TITLEBLOCK)
                                    or u""),
        "dims": bool(settings.get(SETTINGS_PIPE_DIMS, True)),
    }


def sheet_text(pattern, n):
    """A sheet number / name from a pattern: every {n} becomes the number
    ({nn} and {nnn} zero-pad to 2 and 3 digits). A pattern without any
    token gets ' {n}' appended so successive sheets still differ."""
    text = u"{0}".format(pattern or u"").strip()
    if u"{n}" not in text and u"{nn}" not in text and u"{nnn}" not in text:
        text = (text + u" {n}").strip()
    return (text.replace(u"{nnn}", u"{0:03d}".format(int(n)))
                .replace(u"{nn}", u"{0:02d}".format(int(n)))
                .replace(u"{n}", u"{0}".format(int(n))))


def chunks(items, size):
    """items split into runs of `size` (the last may be shorter)."""
    items = list(items or [])
    size = max(1, int(size or 1))
    return [items[i:i + size] for i in range(0, len(items), size)]


def dim_settings(settings):
    """The Dimension Section dialog's remembered values: the dimension
    type name and whether to dimension the chamber's z planes."""
    settings = settings or {}
    mode = settings.get(SETTINGS_DIM_Z_MODE)
    if mode not in (Z_CHAIN, Z_DIRECT):
        mode = Z_CHAIN
    return {"dim_type": u"{0}".format(settings.get(SETTINGS_DIM_TYPE)
                                      or DEFAULT_DIM_TYPE_NAME),
            "z_planes": bool(settings.get(SETTINGS_DIM_Z_PLANES, True)),
            "z_mode": mode,
            "z_skip": bool(settings.get(SETTINGS_DIM_Z_SKIP, True))}


_Z_PLANE_RE = re.compile(r"^\s*[zZ]\s*0*(\d+)\s*$")


def z_plane_number(name):
    """The number of a chamber reference plane named z1, z2, Z10, z05...
    None for any other name."""
    if not name:
        return None
    m = _Z_PLANE_RE.match(u"{0}".format(name))
    return int(m.group(1)) if m else None


def z_plane_order(names):
    """The z-plane names sorted by their number, lowest first, duplicates
    (z1 and z01) keeping the first seen. Names that are not z planes are
    dropped."""
    seen = {}
    for n in names or []:
        k = z_plane_number(n)
        if k is not None and k not in seen:
            seen[k] = n
    return [seen[k] for k in sorted(seen)]


def pick_dim_type_name(names, remembered=u""):
    """Which linear dimension type the dialog offers first: the remembered
    one if it still exists, else the house default (any case), else the
    first name, else None."""
    names = list(names or [])
    if remembered and remembered in names:
        return remembered
    want = DEFAULT_DIM_TYPE_NAME.lower()
    for n in names:
        if u"{0}".format(n).strip().lower() == want:
            return n
    return names[0] if names else None


def size_settings(settings):
    """The shared sizing choice: mode (SIZE_FIXED / SIZE_PARAMS), the
    three parameter names px / py / ph and the clearance (mm)."""
    settings = settings or {}
    mode = settings.get(SETTINGS_SIZE_MODE)
    if mode not in (SIZE_FIXED, SIZE_PARAMS):
        mode = SIZE_FIXED
    return {
        "mode": mode,
        "px": u"{0}".format(settings.get(SETTINGS_SIZE_PARAM_X)
                            or DEFAULT_SIZE_PARAM_X),
        "py": u"{0}".format(settings.get(SETTINGS_SIZE_PARAM_Y)
                            or DEFAULT_SIZE_PARAM_Y),
        "ph": u"{0}".format(settings.get(SETTINGS_SIZE_PARAM_H)
                            or DEFAULT_SIZE_PARAM_H),
        "clear": _mm(settings.get(SETTINGS_SIZE_CLEAR), DEFAULT_SIZE_CLEAR_MM)
        if settings.get(SETTINGS_SIZE_CLEAR) not in (0, 0.0, "0")
        else 0.0,
    }


def section_box_from_dims(side_idx, dx, dy, dh, clear):
    """One side's section crop from the chamber's dimensions: dx along its
    local X, dy along local Y, dh high, with `clear` free each side (all
    in one unit). Sides 0 and 2 look along local X, 1 and 3 along local Y.
    Returns (plane_offset, half_w, half_h, depth): the plane sits
    plane_offset from the chamber centre, the crop is half_w / half_h each
    way of the centre, and depth is the far clip from the plane inward -
    right through the chamber and `clear` past its far face."""
    along, across = (dx, dy) if side_idx in (0, 2) else (dy, dx)
    return (along * 0.5 + clear, across * 0.5 + clear, dh * 0.5 + clear,
            along + 2.0 * clear)


def plan_crop_from_dims(dx, dy, clear):
    """(half_x, half_y) of a plan crop around a chamber dx by dy in plan
    with `clear` free each side."""
    return dx * 0.5 + clear, dy * 0.5 + clear


def plans_settings(settings):
    """The Chamber Plans dialog's remembered values: template (the
    dropdown label - PLANS_TEMPLATE_ACTIVE, PLANS_TEMPLATE_NONE or a
    template name), seed (scope box name, '' for the default pick),
    extents (EXTENTS_SCOPE / EXTENTS_CROP) and the fixed crop width /
    depth (mm)."""
    settings = settings or {}
    tmpl = settings.get(SETTINGS_PLANS_TEMPLATE) or PLANS_TEMPLATE_ACTIVE
    ext = settings.get(SETTINGS_PLANS_EXTENTS)
    if ext not in (EXTENTS_SCOPE, EXTENTS_CROP):
        ext = EXTENTS_CROP
    return {
        "template": u"{0}".format(tmpl),
        "seed": u"{0}".format(settings.get(SETTINGS_PLANS_SEED) or u""),
        "extents": ext,
        "width": _mm(settings.get(SETTINGS_PLANS_WIDTH),
                     DEFAULT_PLANS_WIDTH_MM),
        "depth": _mm(settings.get(SETTINGS_PLANS_DEPTH),
                     DEFAULT_PLANS_DEPTH_MM),
        "workset": u"{0}".format(settings.get(SETTINGS_PLANS_WORKSET) or u""),
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


# ---------------------------------------------------------------------------
# Reference-plane dimension RULES: "z 1-5 chain", "x all", "y 2,3,5 direct"
# ---------------------------------------------------------------------------
_PLANE_RE = re.compile(r"^\s*([xyzXYZ])\s*0*(\d+)\s*$")


def plane_number(name, axis):
    """The number of a reference plane named <axis><number> (x1, Y02,
    z 5 ...) for the given axis letter, else None."""
    if not name or not axis:
        return None
    m = _PLANE_RE.match(u"{0}".format(name))
    if not m or m.group(1).lower() != axis.lower():
        return None
    return int(m.group(2))


def parse_plane_spec(spec):
    """Which plane numbers a rule wants. Accepts:
        'all' or ''   -> every plane found, in number order
        '1-'          -> from 1 up to the highest found
        '1-5'         -> 1, 2, 3, 4, 5 (a descending range is reversed)
        '2,3,5'       -> exactly those (commas, spaces or semicolons)
        '1-3,5'       -> ranges and numbers mixed
    Returns {"kind": "all" | "from" | "exact", "start": int, "nums": [..]}
    or None when the text makes no sense."""
    text = u"{0}".format(spec or u"").strip().lower()
    if text in (u"", u"all", u"*"):
        return {"kind": "all", "start": None, "nums": []}
    m = re.match(r"^(\d+)\s*-\s*$", text)
    if m:
        return {"kind": "from", "start": int(m.group(1)), "nums": []}
    nums = []
    for part in re.split(r"[,;\s]+", text):
        if not part:
            continue
        m = re.match(r"^(\d+)\s*-\s*(\d+)$", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            step = 1 if b >= a else -1
            for n in range(a, b + step, step):
                if n not in nums:
                    nums.append(n)
            continue
        if not part.isdigit():
            return None
        n = int(part)
        if n not in nums:
            nums.append(n)
    if not nums:
        return None
    return {"kind": "exact", "start": None, "nums": nums}


def wanted_numbers(parsed, available):
    """The plane numbers a parsed spec asks for, given the numbers that
    exist on the chamber (any order). 'all' and 'from' only ever name
    planes that exist; 'exact' names what was typed, missing or not."""
    have = sorted(set(int(n) for n in (available or [])))
    if parsed is None:
        return []
    if parsed["kind"] == "all":
        return have
    if parsed["kind"] == "from":
        return [n for n in have if n >= parsed["start"]]
    return list(parsed["nums"])


def pipes_rule(cats=None, cols=True, rows=True):
    """A centreline-string rule: which categories (default all three) and
    which strings (column spacing above the bank, row spacing left of it).
    None when nothing is left to draw."""
    return normalise_rule({"kind": RULE_PIPES, "cats": cats, "cols": cols,
                           "rows": rows})


def normalise_rule(rule):
    """A rule dict cleaned up. Kind 'planes' (the default): axis in
    DIM_AXES, spec text, mode 'chain' | 'direct', skip and inside bools.
    Kind 'pipes': cats (subset of PIPE_CATS in that order), cols and rows
    bools. None when the rule is unusable."""
    if not isinstance(rule, dict):
        return None
    kind = u"{0}".format(rule.get("kind") or RULE_PLANES).strip().lower()
    if kind == RULE_PIPES:
        raw = rule.get("cats")
        if raw is None:
            raw = PIPE_CATS
        if hasattr(raw, "strip"):        # one name given as text
            raw = [raw]
        have = set(u"{0}".format(c).strip().lower() for c in raw)
        cats = [c for c in PIPE_CATS if c in have]
        cols = bool(rule.get("cols", True))
        rows = bool(rule.get("rows", True))
        if not cats or not (cols or rows):
            return None
        return {"kind": RULE_PIPES, "cats": cats, "cols": cols, "rows": rows}
    if kind != RULE_PLANES:
        return None
    axis = u"{0}".format(rule.get("axis") or u"").strip().lower()
    if axis not in DIM_AXES:
        return None
    spec = u"{0}".format(rule.get("spec") if rule.get("spec") is not None
                         else u"all").strip()
    if parse_plane_spec(spec) is None:
        return None
    mode = rule.get("mode")
    if mode not in (Z_CHAIN, Z_DIRECT):
        mode = Z_CHAIN
    return {"kind": RULE_PLANES, "axis": axis, "spec": spec or u"all",
            "mode": mode, "skip": bool(rule.get("skip", True)),
            "inside": bool(rule.get("inside", True))}


def rule_label(rule):
    """'z  1-5  chain  (skip missing)' or 'pipes + conduits  centrelines:
    columns above + rows left' for the list box."""
    r = normalise_rule(rule)
    if r is None:
        return u"(invalid rule)"
    if r["kind"] == RULE_PIPES:
        strings = ([u"columns above"] if r["cols"] else []) + \
                  ([u"rows left"] if r["rows"] else [])
        return u"{0}  centrelines: {1}".format(
            u" + ".join(c + u"s" for c in r["cats"]), u" + ".join(strings))
    return u"{0}  {1}  {2}{3}{4}".format(
        r["axis"], r["spec"],
        u"chain" if r["mode"] == Z_CHAIN else u"direct",
        u"  (skip missing)" if r["skip"] else u"  (all or nothing)",
        u"  (inside the outline)" if r["inside"] else u"")


def default_rules(settings=None):
    """The rule list to start from when none is saved: the old single z
    setting carried over (z, from 1 to the highest, its chain / direct and
    skip choices), or nothing when the old z string was turned off."""
    old = dim_settings(settings)
    if not old["z_planes"]:
        return []
    return [{"kind": RULE_PLANES, "axis": "z", "spec": "1-",
             "mode": old["z_mode"], "skip": old["z_skip"], "inside": True}]


def dim_rules(settings):
    """The saved plane-string rules, cleaned; the carried-over default
    when nothing is saved yet."""
    settings = settings or {}
    raw = settings.get(SETTINGS_DIM_RULES)
    if not isinstance(raw, list):
        return default_rules(settings)
    out = []
    for r in raw:
        n = normalise_rule(r)
        if n is not None:
            out.append(n)
    return out


CUSTOM_SET_LABEL = u"(custom - the list below)"


def normalise_dim_set(raw):
    """A saved dimension set cleaned: {'rules': [rule...]}. A set saved
    when the pipe strings were a separate tick ('pipes': True) gets a
    centreline rule put first, so it still draws what it did. None when
    it is not a dict."""
    if not isinstance(raw, dict):
        return None
    rules = []
    for r in raw.get("rules") or []:
        n = normalise_rule(r)
        if n is not None:
            rules.append(n)
    if raw.get("pipes") and not any(r["kind"] == RULE_PIPES for r in rules):
        rules.insert(0, pipes_rule())
    return {"rules": rules}


def clean_set_name(name):
    """The set name trimmed to one line; u'' when there is nothing in it."""
    if name is None:
        return u""
    return u" ".join(u"{0}".format(name).split())


def dim_sets(settings):
    """{name: set} of the saved dimension sets, cleaned; bad entries and
    blank names dropped."""
    raw = (settings or {}).get(SETTINGS_DIM_SETS)
    out = {}
    if isinstance(raw, dict):
        for name, one in raw.items():
            key = clean_set_name(name)
            n = normalise_dim_set(one)
            if key and n is not None:
                out[key] = n
    return out


def dim_set_names(sets):
    """The set names in dropdown order (case-insensitive alphabetical)."""
    return sorted(sets or {}, key=lambda n: (n.lower(), n))


def same_dim_set(a, b):
    """True when two sets ask for the same dimensions."""
    a = normalise_dim_set(a)
    b = normalise_dim_set(b)
    if a is None or b is None:
        return False
    return a["rules"] == b["rules"]


def matching_dim_set(sets, rules):
    """The name of the saved set equal to this rule list, or None: what
    the dropdown shows when the dialog opens with the last-used list."""
    want = {"rules": rules}
    for name in dim_set_names(sets):
        if same_dim_set(sets[name], want):
            return name
    return None


def _plain_sets(sets):
    # JSON-friendly copy for the settings file
    return dict((k, {"rules": [dict(r) for r in v["rules"]]})
                for k, v in sets.items())


def store_dim_set(settings, name, rules):
    """Save the rule list under name in settings. Returns (name, replaced)
    with the cleaned name, or (None, False) when the name is blank."""
    key = clean_set_name(name)
    if not key:
        return None, False
    sets = dim_sets(settings)
    replaced = key in sets
    sets[key] = normalise_dim_set({"rules": rules})
    settings[SETTINGS_DIM_SETS] = _plain_sets(sets)
    return key, replaced


def drop_dim_set(settings, name):
    """Remove a saved set; True when there was one to remove."""
    key = clean_set_name(name)
    sets = dim_sets(settings)
    if key not in sets:
        return False
    del sets[key]
    settings[SETTINGS_DIM_SETS] = _plain_sets(sets)
    return True


def positions_from_segments(origins, values, line_dir, along):
    """Where a chain dimension's references sit along an axis.

    origins / values: each segment's midpoint (3-tuples) and length, in
    geometric order along line_dir (a unit 3-tuple); along: a function
    mapping a 3-tuple to the axis coordinate of interest. Returns the N+1
    reference positions on that axis, in geometric order. A two-reference
    dimension has one segment (its own origin and value)."""
    pts = []
    for i, (o, v) in enumerate(zip(origins, values)):
        half = 0.5 * float(v or 0.0)
        start = (o[0] - line_dir[0] * half, o[1] - line_dir[1] * half,
                 o[2] - line_dir[2] * half)
        end = (o[0] + line_dir[0] * half, o[1] + line_dir[1] * half,
               o[2] + line_dir[2] * half)
        if i == 0:
            pts.append(along(start))
        pts.append(along(end))
    return pts


def outside_span(positions, low, high, tol=0.0):
    """Indexes (into positions, sorted ascending first) that fall outside
    [low - tol, high + tol] - the planes a chain reaches to that lie beyond
    the chamber's visible box."""
    order = sorted(range(len(positions)), key=lambda i: positions[i])
    out = []
    for rank, i in enumerate(order):
        p = positions[i]
        if p < low - tol or p > high + tol:
            out.append(rank)
    return out
