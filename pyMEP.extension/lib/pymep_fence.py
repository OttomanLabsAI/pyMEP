# -*- coding: utf-8 -*-
"""Fence - place a family along a picked line, draped onto a picked
terrain: the pure maths, the spacing-configuration store and the
FENCE REGISTRY.

PURE PYTHON (no Revit imports) so the CPython suite tests it all:
stations() turns line length + spacing + justification + endpoints
into distances along the line, point_at() walks the tessellated
polyline to a point and its plan tangent, and the config helpers
edit the named spacing configurations kept in pyMEP settings (so
they survive updates with the rest of pyMEP_settings.json).

Every placed fence is recorded in the project's file store
(<exports>/<model>/project_files/fences.json): the LINE's UniqueId,
the TERRAIN's UniqueId, the settings used, and each instance's
UniqueId + station + rotation - so Update Fence can move the posts
back onto the line and the ground after either changes.
"""

import json
import os

import pymep_json

SETTINGS_CONFIGS = "fence_configs"   # {name: {spacing_mm, endpoints}}
SETTINGS_LAST = "fence_config"       # last used config name
SETTINGS_JUSTIFY = "fence_justify"   # start | centre | end
SETTINGS_FAMILY = "fence_family"     # last family label
SETTINGS_MARK = "fence_mark"         # MARK numbering on/off (global)
SETTINGS_MARK_PREFIX = "fence_mark_prefix"
SETTINGS_TOC = "fence_toc"           # TOC population on/off (global)
SETTINGS_TOC_PARAM = "fence_toc_param"
SETTINGS_TOC_FORMULA = "fence_toc_formula"

TOC_PARAM = "TOC"                    # default parameter name

DEFAULT_NAME = "Default"

# foundations get the placement point's survey coordinates written
# into instance parameters of these names (per-config overridable)
EASTING_PARAM = "EASTINGS"
NORTHING_PARAM = "NORTHINGS"

# where the terrain comes from: AUTO (the default) - every topo
# whose footprint the lines touch - the named element(s) stored in
# the config, or picked when the button runs
TERRAIN_PICK = "pick"
TERRAIN_NAMED = "named"
TERRAIN_AUTO = "auto"

DEFAULT_CONFIG = {"spacing_mm": 2000.0, "endpoints": True,
                  "rotation_deg": 0.0, "post": "", "foundation": "",
                  "same_ends": True, "end_post": "",
                  "end_foundation": "",
                  "line_style": "", "priority": 99,
                  "end_priority": False, "panel": "",
                  "panel_width_param": "",
                  "easting_param": EASTING_PARAM,
                  "northing_param": NORTHING_PARAM,
                  "terrain_mode": TERRAIN_AUTO,
                  "terrains": []}


def _terrain_mode(v):
    v = str(v or "").strip().lower()
    return v if v in (TERRAIN_NAMED, TERRAIN_PICK) else TERRAIN_AUTO


def _terrain_list(v):
    if not isinstance(v, (list, tuple)):
        return []
    out = []
    for n in v:
        n = str(n or "").strip()
        if n and n not in out:
            out.append(n)
    return out


def mark_settings(settings):
    """(enabled, prefix) for MARK numbering - GLOBAL, ticked on the
    Fence Configurations window; the prefix lands in front of every
    number (FF -> FF1, FF2, corners FF7A1...)."""
    return (bool(settings.get(SETTINGS_MARK, False)),
            str(settings.get(SETTINGS_MARK_PREFIX) or "").strip())


def toc_settings(settings):
    """(enabled, parameter name, equation) for TOC population -
    GLOBAL, set on the Fence Configurations window. The equation is
    evaluated per foundation with eval_toc()."""
    return (bool(settings.get(SETTINGS_TOC, False)),
            str(settings.get(SETTINGS_TOC_PARAM) or
                TOC_PARAM).strip() or TOC_PARAM,
            str(settings.get(SETTINGS_TOC_FORMULA) or "").strip())


def eval_toc(formula, z_mm, e_m=0.0, n_m=0.0):
    """Evaluate the TOC equation at one foundation.

    Variables: ``z`` = the draped GROUND level at the post
    (millimetres, survey basis), ``e`` / ``n`` = the survey
    EASTING / NORTHING (metres) - plus abs, min, max, round and the
    math functions (sqrt, floor, ceil, sin, cos, tan, atan, atan2,
    log, exp, pow, pi). An empty equation returns ``z`` untouched.
    Returns the value in MILLIMETRES; raises ValueError with the
    reason when the equation cannot be evaluated."""
    if not str(formula or "").strip():
        return float(z_mm)
    import math as _math
    ns = {"z": float(z_mm), "e": float(e_m), "n": float(n_m),
          "abs": abs, "min": min, "max": max, "round": round}
    for nm in ("sqrt", "floor", "ceil", "sin", "cos", "tan",
               "atan", "atan2", "log", "exp", "pow", "pi", "fabs"):
        ns[nm] = getattr(_math, nm)
    try:
        v = eval(compile(str(formula), "<toc>", "eval"),
                 {"__builtins__": {}}, ns)
        return float(v)
    except Exception as ex:
        raise ValueError("TOC equation failed: {}".format(ex))

# the categories a POST may come from / the FOUNDATION must come from
POST_CATEGORIES = ["OST_GenericModel", "OST_Columns",
                   "OST_StructuralColumns"]
FOUNDATION_CATEGORIES = ["OST_StructuralFoundation"]
PANEL_CATEGORIES = ["OST_GenericModel", "OST_StructuralFraming"]

# bays shorter than this get no panel (touching double posts)
PANEL_MIN_MM = 100.0

JUSTIFY_START = "start"
JUSTIFY_CENTRE = "centre"
JUSTIFY_END = "end"

# hard stop for a typo'd spacing (1 mm on a 40 m line = 40k families)
MAX_INSTANCES = 2000


# ---------------------------------------------------------------------------
# geometry
# ---------------------------------------------------------------------------
def poly_length(poly):
    """3D length of a tessellated polyline [(x, y, z), ...]."""
    total = 0.0
    for i in range(1, len(poly)):
        a, b = poly[i - 1], poly[i]
        total += ((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2 +
                  (b[2] - a[2]) ** 2) ** 0.5
    return total


def is_closed(poly, tol=1e-6):
    if len(poly) < 3:
        return False
    a, b = poly[0], poly[-1]
    return ((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2 +
            (b[2] - a[2]) ** 2) ** 0.5 <= tol


def stations(length, spacing, justify=JUSTIFY_START, endpoints=True,
             closed=False, tol=1e-6):
    """Distances along the line where instances go (0 = line start),
    sorted.

    justify 'start': the spacing counts from the start; 'end': from
    the far end; 'centre': the pattern is centred, so the leftover
    splits evenly between both ends. ``endpoints`` True forces an
    instance at 0 and at length, False removes them. Spacing missing
    or not positive -> endpoints only. ``closed`` (a loop): the two
    ends are the same point, so the station at ``length`` is dropped
    whenever one sits at 0."""
    if length is None or length <= tol:
        return [0.0] if endpoints else []
    marks = []
    if spacing and spacing > tol:
        n = int((length + tol) / spacing)    # full steps that fit
        if justify == JUSTIFY_END:
            marks = [length - k * spacing for k in range(n + 1)]
        elif justify == JUSTIFY_CENTRE:
            first = (length - n * spacing) / 2.0
            marks = [first + k * spacing for k in range(n + 1)]
        else:
            marks = [k * spacing for k in range(n + 1)]
    out = []
    eps = max(tol, 1e-9 * length)

    def _add(d):
        for q in out:
            if abs(q - d) <= eps:
                return
        out.append(d)

    if endpoints:
        _add(0.0)
    for d in sorted(marks):
        if d < -eps or d > length + eps:
            continue
        d = min(max(d, 0.0), length)
        if not endpoints and (d <= eps or d >= length - eps):
            continue
        _add(d)
    if endpoints:
        _add(length)
    if closed:
        has0 = any(abs(d) <= eps for d in out)
        if has0:
            out = [d for d in out if abs(d - length) > eps]
    return sorted(out)


def _seg_dir(a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    h = (dx * dx + dy * dy) ** 0.5
    if h <= 1e-12:
        return (1.0, 0.0)
    return (dx / h, dy / h)


def point_at(poly, dist):
    """(point, tangent) at ``dist`` along the polyline - the point is
    an (x, y, z) tuple, the tangent the containing segment's unit
    plan direction. Clamped to the ends; a degenerate polyline gets
    tangent (1, 0)."""
    if not poly:
        return None, (1.0, 0.0)
    if len(poly) == 1:
        return tuple(poly[0]), (1.0, 0.0)
    if dist <= 0.0:
        return tuple(poly[0]), _seg_dir(poly[0], poly[1])
    run = 0.0
    for i in range(1, len(poly)):
        a, b = poly[i - 1], poly[i]
        d = ((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2 +
             (b[2] - a[2]) ** 2) ** 0.5
        if d <= 1e-12:
            continue
        if run + d >= dist:
            t = (dist - run) / d
            return ((a[0] + (b[0] - a[0]) * t,
                     a[1] + (b[1] - a[1]) * t,
                     a[2] + (b[2] - a[2]) * t), _seg_dir(a, b))
        run += d
    return tuple(poly[-1]), _seg_dir(poly[-2], poly[-1])


# ---------------------------------------------------------------------------
# configuration store (operates on the pyMEP settings dict)
# ---------------------------------------------------------------------------
def _num(v, default):
    try:
        return float(v)
    except Exception:
        return default


def get_configs(settings):
    """The saved configs as {name: {spacing_mm, endpoints}} - ALWAYS
    at least 'Default'."""
    raw = settings.get(SETTINGS_CONFIGS)
    out = {}
    if isinstance(raw, dict):
        for name, c in raw.items():
            try:
                sp = float(c.get("spacing_mm") or 0.0)
                if sp <= 0:
                    continue
                try:
                    rot = float(c.get("rotation_deg") or 0.0)
                except Exception:
                    rot = 0.0
                out[str(name)] = {"spacing_mm": sp,
                                  "endpoints": bool(
                                      c.get("endpoints", True)),
                                  "rotation_deg": rot,
                                  "post": str(c.get("post") or ""),
                                  "foundation":
                                      str(c.get("foundation") or ""),
                                  "same_ends": bool(
                                      c.get("same_ends", True)),
                                  "end_post":
                                      str(c.get("end_post") or ""),
                                  "end_foundation":
                                      str(c.get("end_foundation")
                                          or ""),
                                  "line_style":
                                      str(c.get("line_style") or ""),
                                  "priority": int(_num(
                                      c.get("priority"), 99)),
                                  "end_priority": bool(
                                      c.get("end_priority",
                                            False)),
                                  "panel":
                                      str(c.get("panel") or ""),
                                  "panel_width_param":
                                      str(c.get("panel_width_param")
                                          or ""),
                                  "easting_param":
                                      str(c.get("easting_param")
                                          or EASTING_PARAM),
                                  "northing_param":
                                      str(c.get("northing_param")
                                          or NORTHING_PARAM),
                                  "terrain_mode": _terrain_mode(
                                      c.get("terrain_mode")),
                                  "terrains": _terrain_list(
                                      c.get("terrains"))}
            except Exception:
                continue
    if not out:
        out[DEFAULT_NAME] = dict(DEFAULT_CONFIG)
    return out


def upsert_config(settings, name, spacing_mm, endpoints,
                  rotation_deg=0.0, foundation="", post="",
                  same_ends=True, end_post="", end_foundation="",
                  line_style="", priority=99, end_priority=False,
                  panel="", panel_width_param="",
                  easting_param="", northing_param="",
                  terrain_mode=TERRAIN_AUTO, terrains=None):
    """Create or update config ``name`` from the dialog fields;
    returns the configs dict. Raises ValueError with the reason the
    dialog should show. ``rotation_deg`` is the EXTRA rotation on top
    of line-aligned (90 = across the line); any number, also
    negative. ``post`` and ``foundation`` are family labels
    ('Family : Type') - the post from Generic Models / Columns /
    Structural Columns, the foundation from Structural Foundations;
    either may be empty ('none')."""
    name = (name or "").strip()
    if not name:
        raise ValueError("the configuration needs a name")
    try:
        spacing_mm = float(spacing_mm)
    except Exception:
        raise ValueError("spacing must be a positive number (mm)")
    if spacing_mm <= 0:
        raise ValueError("spacing must be a positive number (mm)")
    try:
        rotation_deg = float(rotation_deg or 0.0)
    except Exception:
        raise ValueError("rotation must be a number (degrees)")
    try:
        priority = int(float(priority if priority not in
                             (None, "") else 99))
    except Exception:
        raise ValueError("priority must be a whole number "
                         "(1 = highest)")
    cfgs = get_configs(settings)
    cfgs[name] = {"spacing_mm": spacing_mm,
                  "endpoints": bool(endpoints),
                  "rotation_deg": rotation_deg,
                  "post": str(post or "").strip(),
                  "foundation": str(foundation or "").strip(),
                  "same_ends": bool(same_ends),
                  "end_post": str(end_post or "").strip(),
                  "end_foundation":
                      str(end_foundation or "").strip(),
                  "line_style": str(line_style or "").strip(),
                  "priority": priority,
                  "end_priority": bool(end_priority),
                  "panel": str(panel or "").strip(),
                  "panel_width_param":
                      str(panel_width_param or "").strip(),
                  "easting_param":
                      str(easting_param or "").strip() or
                      EASTING_PARAM,
                  "northing_param":
                      str(northing_param or "").strip() or
                      NORTHING_PARAM,
                  "terrain_mode": _terrain_mode(terrain_mode),
                  "terrains": _terrain_list(terrains)}
    settings[SETTINGS_CONFIGS] = cfgs
    return cfgs


def end_families(cfg):
    """(post, foundation) labels for the line's ENDPOINT stations -
    the in-between pair when 'keep them the same' is on, the
    dedicated end pair when it is off."""
    if cfg.get("same_ends", True):
        return (str(cfg.get("post") or ""),
                str(cfg.get("foundation") or ""))
    return (str(cfg.get("end_post") or ""),
            str(cfg.get("end_foundation") or ""))


def places_something(cfg):
    """False when the configuration would put NOTHING on the line -
    no in-between families and (same ends, endpoints off, or no end
    families either)."""
    if cfg.get("post") or cfg.get("foundation"):
        return True
    ep, ef = end_families(cfg)
    return bool(cfg.get("endpoints", True) and (ep or ef))


def effective_config(settings, name, snapshot):
    """The values an update should USE: the CURRENT saved config of
    that name when it still exists (so edits made with Fence
    Configurations flow into Update Fence), else the record's stored
    snapshot. Returns {spacing_mm, endpoints, rotation_deg, post,
    foundation}. A config saved before posts joined configs (no
    'post' key stored) keeps the record's placed family."""
    snap_post = str(snapshot.get("post") or
                    snapshot.get("family") or "")
    raw = settings.get(SETTINGS_CONFIGS)
    if isinstance(raw, dict) and name and name in raw:
        cfg = get_configs(settings).get(name)
        if cfg is not None:
            out = dict(cfg)
            if "post" not in (raw.get(name) or {}):
                out["post"] = snap_post
            return out
    try:
        rot = float(snapshot.get("rotation_deg") or 0.0)
    except Exception:
        rot = 0.0
    return {"spacing_mm": float(snapshot.get("spacing_mm") or 0.0),
            "endpoints": bool(snapshot.get("endpoints", True)),
            "rotation_deg": rot,
            "post": snap_post,
            "foundation": str(snapshot.get("foundation") or ""),
            "same_ends": bool(snapshot.get("same_ends", True)),
            "end_post": str(snapshot.get("end_post") or ""),
            "end_foundation":
                str(snapshot.get("end_foundation") or ""),
            "line_style": str(snapshot.get("line_style") or ""),
            "priority": int(_num(snapshot.get("priority"), 99)),
            "end_priority": bool(snapshot.get("end_priority",
                                              False)),
            "panel": str(snapshot.get("panel") or ""),
            "panel_width_param":
                str(snapshot.get("panel_width_param") or ""),
            "easting_param":
                str(snapshot.get("easting_param") or EASTING_PARAM),
            "northing_param":
                str(snapshot.get("northing_param") or
                    NORTHING_PARAM),
            "terrain_mode": _terrain_mode(
                snapshot.get("terrain_mode")),
            "terrains": _terrain_list(snapshot.get("terrains"))}


def delete_config(settings, name):
    """Remove config ``name``; the last one standing is never deleted
    - deleting it just resets the store to 'Default'."""
    cfgs = get_configs(settings)
    if name in cfgs:
        del cfgs[name]
    if not cfgs:
        cfgs[DEFAULT_NAME] = dict(DEFAULT_CONFIG)
    settings[SETTINGS_CONFIGS] = cfgs
    return cfgs


# ---------------------------------------------------------------------------
# fence registry (the project's file store folder is the ``base``)
# ---------------------------------------------------------------------------
REGISTRY = "fences.json"


def load_fences(base):
    """{"fences": [...]} - missing / corrupt -> fresh empty (never
    raises)."""
    try:
        with open(os.path.join(base, REGISTRY), "r") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("fences"),
                                                 list):
            return data
    except Exception:
        pass
    return {"fences": []}


def save_fences(base, data):
    if not os.path.isdir(base):
        os.makedirs(base)
    with open(os.path.join(base, REGISTRY), "w") as f:
        pymep_json.dump(data, f, indent=2, sort_keys=True)


def add_fence(base, record):
    """Store a new fence record; assigns and returns its id."""
    data = load_fences(base)
    next_id = 1 + max([0] + [int(r.get("id") or 0)
                             for r in data["fences"]])
    record["id"] = next_id
    data["fences"].append(record)
    save_fences(base, data)
    return next_id


def update_fence(base, record):
    """Replace the record with the same id (appends when unknown)."""
    data = load_fences(base)
    out = [r for r in data["fences"]
           if r.get("id") != record.get("id")]
    out.append(record)
    out.sort(key=lambda r: int(r.get("id") or 0))
    data["fences"] = out
    save_fences(base, data)


def drop_fence(base, fence_id):
    data = load_fences(base)
    data["fences"] = [r for r in data["fences"]
                      if r.get("id") != fence_id]
    save_fences(base, data)


def fence_label(rec):
    """One-line description for pickers and reports."""
    if rec.get("kind") == "network":
        return "Fence network {} - {} line(s) ({} post(s))".format(
            rec.get("id") or "?", len(rec.get("lines") or []),
            len(rec.get("instances") or []))
    what = rec.get("family") or rec.get("foundation") or "?"
    return "Fence {} - {} @ {:g} mm, {} ({} post(s))".format(
        rec.get("id") or "?", what,
        float(rec.get("spacing_mm") or 0.0),
        rec.get("justify") or "start",
        len(rec.get("instances") or []))


# ---------------------------------------------------------------------------
# fence NETWORK - lines grouped by shared endpoints, posts packed so
# their circles TOUCH (tangent), corner posts by priority
# ---------------------------------------------------------------------------
def config_for_style(cfgs, style):
    """(name, cfg) of the configuration bound to a line style name,
    or None. Exact match on the config's 'line_style'."""
    if not style:
        return None
    for name in sorted(cfgs.keys()):
        if cfgs[name].get("line_style") == style:
            return name, cfgs[name]
    return None


def seg_intersect(a1, a2, b1, b2):
    """2D intersection of segments a1-a2 / b1-b2 as (t, u) params in
    [0, 1], or None (parallel / out of range). Endpoint touches
    count."""
    x1, y1 = a1[0], a1[1]
    x2, y2 = a2[0], a2[1]
    x3, y3 = b1[0], b1[1]
    x4, y4 = b2[0], b2[1]
    d1x, d1y = x2 - x1, y2 - y1
    d2x, d2y = x4 - x3, y4 - y3
    den = d1x * d2y - d1y * d2x
    scale = max(abs(d1x), abs(d1y), abs(d2x), abs(d2y), 1e-12)
    if abs(den) <= 1e-12 * scale * scale:
        return None
    t = ((x3 - x1) * d2y - (y3 - y1) * d2x) / den
    u = ((x3 - x1) * d1y - (y3 - y1) * d1x) / den
    e = 1e-9
    if -e <= t <= 1 + e and -e <= u <= 1 + e:
        return (min(max(t, 0.0), 1.0), min(max(u, 0.0), 1.0))
    return None


def _seg3(a, b):
    return ((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2 +
            (b[2] - a[2]) ** 2) ** 0.5


def poly_intersections(pa, pb):
    """Plan CROSSINGS between two polylines as [(dist_a, dist_b, x,
    y)] - arc-length stations along each. Near-duplicate hits
    (shared vertices of consecutive segments) are merged."""
    out = []
    da = 0.0
    for i in range(1, len(pa)):
        a1, a2 = pa[i - 1], pa[i]
        la = _seg3(a1, a2)
        if la <= 1e-12:
            continue
        db = 0.0
        for j in range(1, len(pb)):
            b1, b2 = pb[j - 1], pb[j]
            lb = _seg3(b1, b2)
            if lb <= 1e-12:
                continue
            hit = seg_intersect(a1, a2, b1, b2)
            if hit is not None:
                t, u = hit
                sa, sb = da + la * t, db + lb * u
                dup = False
                for (qa, qb, _x, _y) in out:
                    if abs(qa - sa) <= 1e-6 and abs(qb - sb) <= 1e-6:
                        dup = True
                        break
                if not dup:
                    out.append((sa, sb,
                                a1[0] + (a2[0] - a1[0]) * t,
                                a1[1] + (a2[1] - a1[1]) * t))
            db += lb
        da += la
    return out


def project_to_poly(poly, x, y):
    """The polyline's closest point to (x, y) in plan: (dist_along,
    dist_away, px, py) - or None for a degenerate polyline."""
    best = None
    run = 0.0
    for i in range(1, len(poly)):
        a, b = poly[i - 1], poly[i]
        seg = _seg3(a, b)
        dx, dy = b[0] - a[0], b[1] - a[1]
        l2 = dx * dx + dy * dy
        if l2 <= 1e-12:
            run += seg
            continue
        t = ((x - a[0]) * dx + (y - a[1]) * dy) / l2
        t = min(max(t, 0.0), 1.0)
        px, py = a[0] + dx * t, a[1] + dy * t
        away = ((x - px) ** 2 + (y - py) ** 2) ** 0.5
        if best is None or away < best[1]:
            best = (run + seg * t, away, px, py)
        run += seg
    return best


def panel_bays(stations, min_len, tol=1e-6, skip=None):
    """Panel placements along one run, from the SORTED post
    stations: [(centre, width)] per bay between consecutive posts.
    Bays shorter than ``min_len`` (touching double posts) get no
    panel, and neither does any bay whose centre falls in a
    ``skip`` span (lo, hi) - the corner-post-to-DOUBLE-post gap is
    foundation clearance, not a fence bay."""
    out = []
    for i in range(1, len(stations)):
        w = stations[i] - stations[i - 1]
        if w < min_len - tol:
            continue
        mid = (stations[i] + stations[i - 1]) / 2.0
        if any(lo - tol <= mid <= hi + tol
               for lo, hi in (skip or [])):
            continue
        out.append((mid, w))
    return out


def bbox2d(polys):
    """Plan-view bounding box (min_x, min_y, max_x, max_y) over one
    or more polylines - None when there are no points."""
    xs, ys = [], []
    for poly in polys or []:
        for p in poly or []:
            xs.append(p[0])
            ys.append(p[1])
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def boxes_overlap_2d(a, b, margin=0.0):
    """True when two (min_x, min_y, max_x, max_y) boxes overlap in
    plan, either grown by ``margin`` - the relevance test AUTO
    terrain uses to match topos to the fence lines."""
    if a is None or b is None:
        return False
    return not (a[2] + margin < b[0] or b[2] + margin < a[0] or
                a[3] + margin < b[1] or b[3] + margin < a[1])


def polys_touch(pa, pb, tol):
    """True when two polylines MEET in plan: they cross, or either
    one's endpoint lies within ``tol`` of the other - the test that
    lets a newly drawn line JOIN an existing fence network."""
    if poly_intersections(pa, pb):
        return True
    for one, other in ((pa, pb), (pb, pa)):
        if len(one) < 1 or len(other) < 2:
            continue
        for pt in (one[0], one[-1]):
            pr = project_to_poly(other, pt[0], pt[1])
            if pr is not None and pr[1] <= tol:
                return True
    return False


def renumber_priorities(settings, ordered_names):
    """Rewrite every config's priority from its LIST position - the
    TOP one (index 0) becomes 1 and wins corner posts in Fence
    Network. Names missing from the list keep their old number."""
    cfgs = get_configs(settings)
    for i, name in enumerate(ordered_names):
        if name in cfgs:
            cfgs[name]["priority"] = i + 1
    settings[SETTINGS_CONFIGS] = cfgs
    return cfgs


def priority_order(cfgs):
    """Config names sorted for the list: priority first (smallest =
    top), name breaks ties."""
    return sorted(cfgs.keys(),
                  key=lambda n: (int(_num(cfgs[n].get("priority"),
                                          99)), n.lower()))


def pick_priority(named_cfgs):
    """The winning (name, cfg) - SMALLEST priority number wins (1 =
    highest, e.g. impact rated); ties break on the config name so
    the answer is stable."""
    best = None
    for name, cfg in named_cfgs:
        pr = int(_num(cfg.get("priority"), 99))
        if best is None or (pr, name) < (int(_num(
                best[1].get("priority"), 99)), best[0]):
            best = (name, cfg)
    return best


def cluster_nodes(points, tol):
    """Group endpoint coordinates into NODES: [(x, y)] -> (centers,
    index_per_point). Points within ``tol`` of a node join it (the
    node keeps its first point's position - fence corner clicks are
    either snapped or not, chains of near-misses do not occur)."""
    centers = []
    idx = []
    for x, y in points:
        found = None
        for i, (cx, cy) in enumerate(centers):
            if ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 <= tol:
                found = i
                break
        if found is None:
            centers.append((x, y))
            found = len(centers) - 1
        idx.append(found)
    return centers, idx


def edge_stations(length, spacing, anchor=0.0, clear_end=None,
                  tol=1e-6):
    """In-between post centers for one stretch, measured from its
    start.

    The first post sits a FULL SPACING from the ``anchor`` - the
    corner itself (0) or the corner's DOUBLE post when one was
    placed on this line - and every next post is ``spacing`` on.
    The run stops clear of the far corner (``clear_end`` when the
    circles are known, one spacing when not): the leftover simply
    SHORTENS the last bay - posts never double up."""
    if not spacing or spacing <= tol or length is None or \
            length <= tol:
        return []
    first = (anchor or 0.0) + spacing
    limit = length - (clear_end if clear_end is not None
                      else spacing)
    out = []
    d = first
    while d <= limit + tol:
        out.append(d)
        d += spacing
    return out


def _letters(i):
    """0 -> A, 1 -> B, ... 25 -> Z, 26 -> AA."""
    out = ""
    i += 1
    while i > 0:
        i, r = divmod(i - 1, 26)
        out = chr(65 + r) + out
    return out


def network_marks(edges, node_xy, start, tol=1e-6, prefix=""):
    """MARK values for every post of a fence network. ``prefix``
    lands in front of every number: 'FF' -> FF1, FF2... corners
    FF7A1. Every post is GUARANTEED a mark - anything the walks
    cannot reach (degenerate geometry) gets a prefix+X1, X2...
    fallback.

    ``edges``: {e: {"poly": [(x, y, z)...], "posts": [(station,
    kind, key), ...], "group": <optional line style / config>}}
    with kind "node" (corner, key = node index), "double" (key as
    the caller wants it back) or "post" (key as the caller wants it
    back); every line END must carry a node. A run only continues
    onto its OWN line or its own group - a different group hangs
    off it as a branch.
    ``node_xy``: [(x, y)] per node index. ``start``: the edge id of
    the HIGHEST-PRIORITY line - numbering begins at its station-0
    end.

    Returns {(kind, key): mark}. The MAIN CHAIN walks from the
    start taking the most CLOCKWISE (rightmost) turn at every
    corner and numbers '1', '2', ... in walk order; when the chain
    closes a loop that runs counter-clockwise it is re-walked the
    other way, so the numbers always run CLOCKWISE. Every line
    hanging OFF a numbered corner is a BRANCH with its OWN system:
    the corner's mark + a letter per branch (clockwise from north)
    + its own running number - '7A1', '7A2'; a second branch at the
    same corner '7B1'; sub-branches recurse ('7A2A1'). Disconnected
    parts get a 'C2-' / 'C3-' prefix and the same treatment."""
    import math as _math

    # ---- segments: node-to-node stretches of each line ---------------
    segs = []               # {"e", "s0", "s1", "n0", "n1", "mid"}
    node_arms = {}          # ni -> [(seg_index, +1 | -1)]
    for e in sorted(edges.keys()):
        posts = sorted(edges[e]["posts"], key=lambda p: p[0])
        node_pos = [(st, key) for st, kind, key in posts
                    if kind == "node"]
        for k in range(len(node_pos) - 1):
            s0, n0 = node_pos[k]
            s1, n1 = node_pos[k + 1]
            mid = [(st, kind, key) for st, kind, key in posts
                   if kind != "node" and s0 + tol < st < s1 - tol]
            si = len(segs)
            segs.append({"e": e, "s0": s0, "s1": s1,
                         "n0": n0, "n1": n1, "mid": mid})
            node_arms.setdefault(n0, []).append((si, 1))
            node_arms.setdefault(n1, []).append((si, -1))

    def _dir(si, direction, at):
        """Unit direction of an arm: 'out' leaving its start node,
        'in' arriving at its far node."""
        sg = segs[si]
        poly = edges[sg["e"]]["poly"]
        if direction > 0:
            t = point_at(poly, sg["s0" if at == "out" else "s1"])[1]
            return t
        t = point_at(poly, sg["s1" if at == "out" else "s0"])[1]
        return (-t[0], -t[1])

    visited = set()
    marks = {}
    groups = dict((e, edges[e].get("group")) for e in edges)

    def _pick_next(node, d_in, cur_e):
        """The arm a run CONTINUES onto: its own line first
        (straight through a T), then its own group (line style) by
        the most CLOCKWISE turn. A different group never continues
        the run - it hangs off it as a BRANCH."""
        cur_g = groups.get(cur_e)
        best = None
        for si, dirn in sorted(node_arms.get(node) or []):
            if si in visited:
                continue
            e2 = segs[si]["e"]
            if e2 == cur_e:
                rank = 0
            elif groups.get(e2) == cur_g:
                rank = 1
            else:
                continue
            do = _dir(si, dirn, "out")
            turn = _math.atan2(d_in[0] * do[1] - d_in[1] * do[0],
                               d_in[0] * do[0] + d_in[1] * do[1])
            key = (rank, turn)
            if best is None or key < best[0]:
                best = (key, si, dirn)
        return None if best is None else (best[1], best[2])

    def _walk(arm, next_k, mark_fn):
        """Traverse from ``arm``, numbering every unmarked post;
        stops at a dead end, a style change or an already-numbered
        corner. Returns (sequence, next_k, nodes seen)."""
        seq, nodes_seen = [], []
        while arm is not None:
            si, dirn = arm
            visited.add(si)
            seq.append(arm)
            sg = segs[si]
            mid = sg["mid"] if dirn > 0 else \
                list(reversed(sg["mid"]))
            for _st, kind, key in mid:
                if (kind, key) not in marks:
                    marks[(kind, key)] = mark_fn(next_k)
                    next_k += 1
            far = sg["n1"] if dirn > 0 else sg["n0"]
            if ("node", far) in marks:
                nodes_seen.append(far)
                break
            marks[("node", far)] = mark_fn(next_k)
            next_k += 1
            nodes_seen.append(far)
            arm = _pick_next(far, _dir(si, dirn, "in"), sg["e"])
        return seq, next_k, nodes_seen

    def _replay(seq, next_k, mark_fn):
        """Re-number along a KNOWN arm sequence (the reversed
        loop)."""
        for si, dirn in seq:
            visited.add(si)
            sg = segs[si]
            mid = sg["mid"] if dirn > 0 else \
                list(reversed(sg["mid"]))
            for _st, kind, key in mid:
                if (kind, key) not in marks:
                    marks[(kind, key)] = mark_fn(next_k)
                    next_k += 1
            far = sg["n1"] if dirn > 0 else sg["n0"]
            if ("node", far) not in marks:
                marks[("node", far)] = mark_fn(next_k)
                next_k += 1
        return next_k

    def _chain(first_node, first_arm, mark_fn):
        """The main run: number the start corner, walk rightmost;
        a loop that came back COUNTER-clockwise is re-walked the
        other way so the numbers run clockwise."""
        marks[("node", first_node)] = mark_fn(1)
        before = set(marks)
        seq, _k, nodes_seen = _walk(first_arm, 2, mark_fn)
        closed = nodes_seen and nodes_seen[-1] == first_node and \
            len(seq) > 1
        if not closed:
            return
        ring = [first_node] + nodes_seen[:-1]
        area = 0.0
        for i in range(len(ring)):
            x0, y0 = node_xy[ring[i]]
            x1, y1 = node_xy[ring[(i + 1) % len(ring)]]
            area += x0 * y1 - x1 * y0
        if area <= 0:
            return                  # already clockwise
        # counter-clockwise: wipe THIS walk's numbers and replay the
        # same loop the other way around
        for h in [h for h in marks if h not in before]:
            del marks[h]
        visited.difference_update(si for si, _d in seq)
        rev = [(si, -dirn) for si, dirn in reversed(seq)]
        _replay(rev, 2, mark_fn)

    def _branches():
        """Grow branches off every numbered corner until none are
        left: per corner, arms sorted CLOCKWISE from north get a
        letter each, and each walks with its own numbers."""
        grown = True
        while grown:
            grown = False
            for ni in sorted([n for n in node_arms
                              if ("node", n) in marks],
                             key=lambda n: (len(marks[("node", n)]),
                                            marks[("node", n)])):
                arms = [(si, d) for si, d in node_arms[ni]
                        if si not in visited]
                if not arms:
                    continue
                def _bearing(arm):
                    dx, dy = _dir(arm[0], arm[1], "out")
                    return (_math.atan2(dx, dy)) % (2 * _math.pi)
                arms.sort(key=_bearing)
                anchor = marks[("node", ni)]
                b_used = 0
                for arm in arms:
                    if arm[0] in visited:
                        continue    # eaten by an earlier branch
                    pref = anchor + _letters(b_used)
                    b_used += 1
                    _walk(arm, 1,
                          (lambda p: lambda k: p + str(k))(pref))
                grown = True
                break               # marked nodes changed - rescan
        return

    # ---- main chain from the start line's station-0 end --------------
    comp = 1
    if start in edges:
        posts0 = sorted(edges[start]["posts"], key=lambda p: p[0])
        n_first = [key for _st, kind, key in posts0
                   if kind == "node"]
        if n_first:
            first_node = n_first[0]
            first_arm = None
            for si, dirn in sorted(node_arms.get(first_node) or []):
                if segs[si]["e"] == start and dirn > 0 and \
                        abs(segs[si]["s0"] -
                            posts0[0][0]) <= tol:
                    first_arm = (si, dirn)
                    break
            if first_arm is None:
                arms0 = sorted(node_arms.get(first_node) or [])
                first_arm = arms0[0] if arms0 else None
            if first_arm is not None:
                _chain(first_node, first_arm,
                       (lambda p: lambda k: p + str(k))(prefix))
                _branches()

    # ---- disconnected parts: their own C2- / C3- systems -------------
    while True:
        left = [si for si in range(len(segs)) if si not in visited]
        if not left:
            break
        comp += 1
        si = left[0]
        pref = "{}C{}-".format(prefix, comp)
        _chain(segs[si]["n0"], (si, 1),
               (lambda p: lambda k: p + str(k))(pref))
        _branches()

    # ---- guarantee: NOTHING stays unnumbered -------------------------
    x = 0
    for e in sorted(edges.keys()):
        for _st, kind, key in sorted(edges[e]["posts"],
                                     key=lambda p: p[0]):
            if (kind, key) not in marks:
                x += 1
                marks[(kind, key)] = "{}X{}".format(prefix, x)

    return marks


def pair_stations(instances, dists, tol=1e-6):
    """MOVE plan: [(instance_dict, new_station), ...] pairing the
    stored instances (by their station order) with the new stations -
    or None when the counts differ and the fence must be rebuilt
    instead."""
    if len(instances) != len(dists) or not instances:
        return None
    inst = sorted(instances,
                  key=lambda r: float(r.get("station_ft") or 0.0))
    return list(zip(inst, sorted(dists)))
