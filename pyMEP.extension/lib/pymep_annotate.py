# -*- coding: utf-8 -*-
"""Annotate - PURE PYTHON (no Revit imports) so the CPython suite
tests it: how a selection of parallel runs is grouped into BANKS, how
a bank's arrangement becomes '2x2', and how the label's parts are
ordered onto one or more lines.

Annotate Pipes labels PIPES or CONDUITS (never both at once). Each
bank - a set of parallel runs travelling together - gets ONE label
built from up to four parts in the order the dialog sets:

    PREFIX        free text, e.g. 'HV'
    COMBINATION   the bank's arrangement ACROSS x UP, e.g. '2x2'
    DIAMETER      '150' (or '100/150' when a bank is mixed)
    SLOPE         '1:150' - pipes only, off by default

each with its own SUFFIX written straight after it (the diameter's
defaults to the diameter symbol, so a bank reads '150\u00d8'), and an
optional line break after any part.
"""

import math

# ---------------------------------------------------------------- items
ITEM_NONE = ""
ITEM_PREFIX = "prefix"
ITEM_COMBO = "combination"
ITEM_DIA = "diameter"
ITEM_SLOPE = "slope"

ITEMS = [ITEM_PREFIX, ITEM_COMBO, ITEM_DIA, ITEM_SLOPE]

ITEM_LABELS = [
    (ITEM_PREFIX, "Prefix"),
    (ITEM_COMBO, "Combination (2x2)"),
    (ITEM_DIA, "Diameter (150)"),
    (ITEM_SLOPE, "Slope (1:150) - pipes only"),
    (ITEM_NONE, "(none)"),
]

SLOTS = 4
DEFAULT_ORDER = [ITEM_PREFIX, ITEM_COMBO, ITEM_DIA, ITEM_NONE]
DEFAULT_BREAKS = [False, False, False]

# what follows each part, straight after it with no space - the
# diameter reads '150\u00d8' the way a drawing writes it, and any of
# them can be changed (' mm', 'no.', 'dia') in the dialog
DEFAULT_SUFFIXES = {ITEM_PREFIX: "", ITEM_COMBO: "",
                    ITEM_DIA: u"\u00d8", ITEM_SLOPE: ""}

SETTINGS_PREFIX = "annotate_prefix"
SETTINGS_TEXT_TYPE = "annotate_text_type"
SETTINGS_ORDER = "annotate_order"
SETTINGS_BREAKS = "annotate_breaks"
SETTINGS_BANK_GAP = "annotate_bank_gap_mm"
SETTINGS_SUFFIXES = "annotate_suffixes"

# two runs belong to one bank when the CLEAR gap between their
# surfaces is no more than this, in mm - a plain distance the dialog
# shows and the user can change, because no multiple of the diameter
# suits both 50 mm conduits at 210 mm centres and a metre-wide
# pipe trench
DEFAULT_BANK_GAP_MM = 600.0

# runs further off parallel than this are different banks
BANK_ANGLE_TOL_DEG = 5.0


def annotate_settings(settings):
    """The dialog's remembered values as a dict - 'prefix',
    'text_type', 'order', 'breaks', 'gap' and 'suffixes' - with
    anything unrecognised repaired."""
    prefix = str(settings.get(SETTINGS_PREFIX) or "")
    ttype = str(settings.get(SETTINGS_TEXT_TYPE) or "")
    order = list(settings.get(SETTINGS_ORDER) or DEFAULT_ORDER)
    clean, seen = [], set()
    for key in order:
        key = str(key or "")
        if key not in ITEMS or key in seen:
            key = ITEM_NONE          # unknown or repeated -> empty slot
        if key:
            seen.add(key)
        clean.append(key)
    clean = (clean + [ITEM_NONE] * SLOTS)[:SLOTS]
    if not [k for k in clean if k]:
        clean = list(DEFAULT_ORDER)
    breaks = list(settings.get(SETTINGS_BREAKS) or DEFAULT_BREAKS)
    breaks = [bool(b) for b in breaks]
    breaks = (breaks + [False] * (SLOTS - 1))[:SLOTS - 1]
    try:
        gap = float(settings.get(SETTINGS_BANK_GAP))
        if gap < 0:
            gap = DEFAULT_BANK_GAP_MM
    except (TypeError, ValueError):
        gap = DEFAULT_BANK_GAP_MM
    suffixes = dict(DEFAULT_SUFFIXES)
    stored = settings.get(SETTINGS_SUFFIXES)
    if isinstance(stored, dict):
        for key in ITEMS:
            if key in stored:
                suffixes[key] = u"{}".format(stored[key] or "")
    return {"prefix": prefix, "text_type": ttype, "order": clean,
            "breaks": breaks, "gap": gap, "suffixes": suffixes}


def compose(values, order, breaks, suffixes=None):
    """The label text: every non-empty part in ``order``, each with
    its SUFFIX appended (no space - '150' + '\u00d8' reads '150\u00d8'),
    joined by a space or - where ``breaks`` says so - a line break. A
    part that resolves to nothing is skipped WITHOUT leaving its
    separator or its suffix behind, so an empty prefix never opens the
    label with a blank line."""
    suffixes = suffixes or {}
    parts = []
    for i, key in enumerate(order):
        text = (values.get(key) or "").strip() if key else ""
        if not text:
            continue
        text = u"{}{}".format(text, suffixes.get(key, "") or "")
        brk = bool(breaks[i]) if i < len(breaks) else False
        parts.append((text, brk))
    out = []
    for i, (text, brk) in enumerate(parts):
        out.append(text)
        if i < len(parts) - 1:
            out.append("\n" if brk else " ")
    return "".join(out)


# ------------------------------------------------------------ geometry
def normalise_dir(dx, dy):
    """A sign-normalised unit XY direction (dx >= 0, or dx == 0 and
    dy >= 0), so a run drawn backwards matches its neighbour. None
    when the run has no XY length."""
    mag = math.sqrt(dx * dx + dy * dy)
    if mag < 1e-12:
        return None
    if dx < 0 or (dx == 0.0 and dy < 0):
        dx, dy = -dx, -dy
    return (dx / mag, dy / mag)


def parallel(a, b, tol_deg=BANK_ANGLE_TOL_DEG):
    """True when two normalised directions run the same way within
    ``tol_deg`` (opposite directions count as parallel)."""
    cross = abs(a[0] * b[1] - a[1] * b[0])
    dot = abs(a[0] * b[0] + a[1] * b[1])
    return math.degrees(math.atan2(cross, dot)) <= tol_deg


def _overlap(a0, a1, b0, b1):
    """How far two 1D spans overlap; negative is the gap between
    them."""
    if a0 > a1:
        a0, a1 = a1, a0
    if b0 > b1:
        b0, b1 = b1, b0
    return min(a1, b1) - max(a0, b0)


def same_bank(a, b, gap_mm=DEFAULT_BANK_GAP_MM,
              tol_deg=BANK_ANGLE_TOL_DEG):
    """Do runs ``a`` and ``b`` travel together? Each is a dict with
    'dir' (normalised XY), 'mid' (x, y, z in mm), 'ends' ((x,y,z),
    (x,y,z) in mm) and 'dia' (mm).

    Three tests: the runs must be PARALLEL, the CLEAR gap between
    their surfaces across the section must be no more than ``gap_mm``,
    and they must OVERLAP along the run (within the same gap) -
    otherwise two conduits in different trenches a hundred metres
    apart would count as one bank just because they line up."""
    if not parallel(a["dir"], b["dir"], tol_deg):
        return False
    ux, uy = a["dir"]
    px, py = -uy, ux                      # horizontal perpendicular
    dx = b["mid"][0] - a["mid"][0]
    dy = b["mid"][1] - a["mid"][1]
    across = dx * px + dy * py
    up = b["mid"][2] - a["mid"][2]
    reach = float(gap_mm)
    clear = math.sqrt(across * across + up * up) - \
        (a["dia"] + b["dia"]) / 2.0
    if clear > reach:
        return False
    # along the run, measured in a's frame
    def _along(item):
        vals = []
        for e in item["ends"]:
            vals.append((e[0] - a["mid"][0]) * ux +
                        (e[1] - a["mid"][1]) * uy)
        return vals
    a0, a1 = _along(a)
    b0, b1 = _along(b)
    return _overlap(a0, a1, b0, b1) >= -reach


def cluster(count, is_same):
    """Group ``count`` items with a pairwise ``is_same(i, j)`` test,
    merging transitively so a row of four chains into one bank.
    Returns lists of indices, each sorted, in first-seen order."""
    parent = list(range(count))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(count):
        for j in range(i + 1, count):
            if find(i) != find(j) and is_same(i, j):
                parent[find(j)] = find(i)
    groups, order = {}, []
    for i in range(count):
        root = find(i)
        if root not in groups:
            groups[root] = []
            order.append(root)
        groups[root].append(i)
    return [groups[r] for r in order]


def group_1d(values, tol):
    """Sorted values grouped into runs no more than ``tol`` apart -
    the columns (or rows) of a bank. Returns a list of groups, each a
    list of the ORIGINAL indices."""
    idx = sorted(range(len(values)), key=lambda i: values[i])
    groups = []
    for i in idx:
        if groups and (values[i] - values[groups[-1][-1]]) <= tol:
            groups[-1].append(i)
        else:
            groups.append([i])
    return groups


def arrangement(cells, tol):
    """(across count, up count, exact, distinct) for a bank whose
    members sit at ``cells`` = [(across mm, up mm), ...].

    Members sharing a cell (a run split into several segments) count
    ONCE. ``exact`` is False when the members do not fill the grid -
    an L of three conduits is not '2x2'."""
    if not cells:
        return (0, 0, False, 0)
    col_of, row_of = {}, {}
    for gi, grp in enumerate(group_1d([c[0] for c in cells], tol)):
        for i in grp:
            col_of[i] = gi
    for gi, grp in enumerate(group_1d([c[1] for c in cells], tol)):
        for i in grp:
            row_of[i] = gi
    cols = len(set(col_of.values()))
    rows = len(set(row_of.values()))
    distinct = len(set((col_of[i], row_of[i])
                       for i in range(len(cells))))
    return (cols, rows, cols * rows == distinct, distinct)


def combo_text(cells, tol):
    """The COMBINATION part: 'across x up' for a filled grid, else a
    plain count so a ragged bank is never described as a rectangle it
    is not."""
    cols, rows, exact, distinct = arrangement(cells, tol)
    if distinct == 0:
        return ""
    if exact:
        return "{}x{}".format(cols, rows)
    return "{} no.".format(distinct)


def dia_text(dias):
    """The DIAMETER part as a bare number - '150', or every distinct
    size when a bank is mixed ('100/150'). The unit or symbol comes
    from the part's SUFFIX, so a drawing can read '150\u00d8' or
    '150 mm' without touching this."""
    vals = sorted(set(int(round(d)) for d in dias if d))
    if not vals:
        return ""
    return "/".join(str(v) for v in vals)


def slope_text(slope):
    """The SLOPE part as Revit shows it: '1:150', or '1:0' level."""
    s = abs(slope or 0.0)
    if s <= 1e-9:
        return "1:0"
    return "1:{}".format(int(round(1.0 / s)))
