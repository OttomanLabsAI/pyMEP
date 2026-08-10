# -*- coding: utf-8 -*-
"""Fence - place a family along a picked line, draped onto a picked
terrain: the pure maths + the spacing-configuration store.

PURE PYTHON (no Revit imports) so the CPython suite tests it all:
stations() turns line length + spacing + justification + endpoints
into distances along the line, point_at() walks the tessellated
polyline to a point and its plan tangent, and the config helpers
edit the named spacing configurations kept in pyMEP settings (so
they survive updates with the rest of pyMEP_settings.json).
"""

SETTINGS_CONFIGS = "fence_configs"   # {name: {spacing_mm, endpoints}}
SETTINGS_LAST = "fence_config"       # last used config name
SETTINGS_JUSTIFY = "fence_justify"   # start | centre | end
SETTINGS_FAMILY = "fence_family"     # last family label

DEFAULT_NAME = "Default"
DEFAULT_CONFIG = {"spacing_mm": 2000.0, "endpoints": True}

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
                out[str(name)] = {"spacing_mm": sp,
                                  "endpoints": bool(
                                      c.get("endpoints", True))}
            except Exception:
                continue
    if not out:
        out[DEFAULT_NAME] = dict(DEFAULT_CONFIG)
    return out


def upsert_config(settings, name, spacing_mm, endpoints):
    """Create or update config ``name`` from the dialog fields;
    returns the configs dict. Raises ValueError with the reason the
    dialog should show."""
    name = (name or "").strip()
    if not name:
        raise ValueError("the configuration needs a name")
    try:
        spacing_mm = float(spacing_mm)
    except Exception:
        raise ValueError("spacing must be a positive number (mm)")
    if spacing_mm <= 0:
        raise ValueError("spacing must be a positive number (mm)")
    cfgs = get_configs(settings)
    cfgs[name] = {"spacing_mm": spacing_mm,
                  "endpoints": bool(endpoints)}
    settings[SETTINGS_CONFIGS] = cfgs
    return cfgs


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
