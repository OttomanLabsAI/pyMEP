# -*- coding: utf-8 -*-
"""Chamber Sheet Setup helpers - PURE PYTHON (no Revit or WPF imports) so
the CPython suite tests them: which views belong to which chamber Mark,
the scale field parser, the remembered settings and the row layout that
puts a chamber's plan and sections in a line on the sheet.

Naming contract: a chamber is known by its whole Mark ("LV1/Z1" - LV
numbers repeat across zones, so the zone part is identity). A view belongs
to the chamber whose Mark its name carries as a whole token: "LV1/Z1",
"LV1/Z1 SIDE A" and "Plan LV1/Z1" are LV1/Z1's, while "LV1/Z10" is not.
When more than one Mark fits a name the longest wins, so a bare "LV1"
never swallows "LV1/Z1". On the sheet the plan view(s) go first, then the
sections by their SIDE letter.

"""
import re

SETTINGS_SHEET_SCALE = "sheet_setup_scale"
SETTINGS_SHEET_GAP = "sheet_setup_gap_mm"
SETTINGS_SHEET_LEFT = "sheet_setup_left_mm"
SETTINGS_SHEET_TOP = "sheet_setup_top_mm"
SETTINGS_SHEET_LABEL = "sheet_setup_label_mm"
SETTINGS_SHEET_PLAN_TEMPLATE = "sheet_setup_plan_template"
SETTINGS_SHEET_SECTION_TEMPLATE = "sheet_setup_section_template"
SETTINGS_SHEET_VIEWPORT_TYPE = "sheet_setup_viewport_type"

LEAVE_TEMPLATE = u"(leave as is)"
DEFAULT_VIEWPORT = u"(Revit default)"

DEFAULT_SCALE = 20
DEFAULT_GAP_MM = 15.0
DEFAULT_LEFT_MM = 20.0
DEFAULT_TOP_MM = 20.0
DEFAULT_LABEL_MM = 12.0
SCALE_CHOICES = (10, 20, 25, 50, 100, 200)

SIDE_RE = re.compile(r"SIDE\s*(?P<letter>[A-Z])(?:_\d+)?\s*$")


from pymep_chamber_sections import chamber_key  # noqa: E402  (shared rule)


def side_letter(name):
    """'A' for a section name ending in 'SIDE A' (a '_2' clash suffix is
    tolerated), else None."""
    if not name:
        return None
    m = SIDE_RE.search(name.strip())
    return m.group("letter") if m else None


def key_from_section_name(name):
    """The chamber key a 'SIDE' section name carries: 'LV1/Z1 SIDE A' and
    'LV1/SIDE A' both give 'LV1'. None when the name has no SIDE letter."""
    if not name or side_letter(name) is None:
        return None
    stem = SIDE_RE.sub(u"", name.strip()).rstrip(u" /_-").strip()
    key = chamber_key(stem)
    return key or None


def _is_word_char(ch):
    return ch.isalnum()


def has_key(name, key):
    """True when `key` appears in `name` as a whole token: the characters
    either side (if any) are not letters or digits. Case-insensitive."""
    if not name or not key:
        return False
    hay = name.lower()
    needle = key.lower()
    start = 0
    while True:
        i = hay.find(needle, start)
        if i < 0:
            return False
        before_ok = i == 0 or not _is_word_char(hay[i - 1])
        j = i + len(needle)
        after_ok = j >= len(hay) or not _is_word_char(hay[j])
        if before_ok and after_ok:
            return True
        start = i + 1


def best_key(name, keys):
    """The key (chamber Mark) a view name belongs to: the LONGEST key that
    appears in the name as a whole token, or None."""
    best = None
    for key in keys:
        if has_key(name, key) and (best is None or len(key) > len(best)):
            best = key
    return best


def group_chamber_views(views, known_marks=None):
    """views: iterable of (name, kind) with kind 'plan' or 'section'.
    Chamber keys are the model's chamber Marks plus the stem of every
    'SIDE' section name. Returns
        {key: {"plans": [plan names...], "sections": [(letter, name)...]}}
    for the keys that own at least one view. Each view goes to the longest
    key its name carries as a whole token, so plain project plans
    ('Level 1') stay out and 'LV1' never takes 'LV1/Z1' views."""
    keys = set()
    for m in known_marks or []:
        k = chamber_key(m)
        if k:
            keys.add(k)
    names = []
    for name, kind in views:
        if not name:
            continue
        name = name.strip()
        names.append((name, kind))
        if kind == "section":
            k = key_from_section_name(name)
            if k:
                keys.add(k)
    groups = {}
    for name, kind in names:
        if kind == "section" and side_letter(name) is None:
            continue
        key = best_key(name, keys)
        if key is None:
            continue
        g = groups.setdefault(key, {"plans": [], "sections": []})
        if kind == "plan":
            g["plans"].append(name)
        else:
            g["sections"].append((side_letter(name), name))
    for g in groups.values():
        g["plans"].sort(key=lambda n: n.lower())
        g["sections"].sort(key=lambda t: (t[0], t[1].lower()))
    return groups


def ordered_views(group):
    """The names in sheet order: plan(s) first, then the sections by
    letter."""
    out = list(group.get("plans") or [])
    for _letter, name in group.get("sections") or []:
        out.append(name)
    return out


def group_label(key, group):
    """'LV1   (plan LV1/Z1 + 3 sections)' for the tick list."""
    plans = group.get("plans") or []
    n = len(group.get("sections") or [])
    if len(plans) == 1:
        head = u"plan {0}".format(plans[0])
    elif plans:
        head = u"{0} plans".format(len(plans))
    else:
        head = u"no plan"
    return u"{0}   ({1} + {2} section{3})".format(
        key, head, n, "" if n == 1 else "s")


def parse_scale(text):
    """'1:20', '1 : 20', '1/20' or '20' -> 20. None unless a positive
    whole number comes out of it."""
    if text is None:
        return None
    try:
        s = text.strip().replace(" ", "")
    except Exception:
        return None
    if not s:
        return None
    for sep in (":", "/"):
        if sep in s:
            head, _sep, tail = s.partition(sep)
            if head not in ("1", ""):
                return None
            s = tail
            break
    if not s.isdigit():
        return None
    n = int(s)
    if n <= 0:
        return None
    return n


def scale_text(n):
    return "1:{0}".format(int(n))


def _num(value, default):
    # None-safe: float(None) is a CLR SystemError under IronPython.
    if value is None:
        return default
    try:
        v = float(value)
    except Exception:
        return default
    if v != v or v - v != 0 or v < 0:
        return default
    return v


def sheet_settings(settings):
    """The dialog's remembered values: scale (int), gap / left / top /
    label (mm floats), plan_template / section_template (names, "" for
    none). Missing or broken entries fall back to defaults."""
    settings = settings or {}
    scale = parse_scale(u"{0}".format(settings.get(SETTINGS_SHEET_SCALE) or ""))
    return {
        "scale": scale or DEFAULT_SCALE,
        "gap": _num(settings.get(SETTINGS_SHEET_GAP), DEFAULT_GAP_MM),
        "left": _num(settings.get(SETTINGS_SHEET_LEFT), DEFAULT_LEFT_MM),
        "top": _num(settings.get(SETTINGS_SHEET_TOP), DEFAULT_TOP_MM),
        "label": _num(settings.get(SETTINGS_SHEET_LABEL), DEFAULT_LABEL_MM),
        "plan_template": settings.get(SETTINGS_SHEET_PLAN_TEMPLATE) or u"",
        "section_template": (settings.get(SETTINGS_SHEET_SECTION_TEMPLATE)
                             or u""),
        "viewport_type": settings.get(SETTINGS_SHEET_VIEWPORT_TYPE) or u"",
    }


def template_choice(text):
    """The template name a dropdown selection means: '' for the
    '(leave as is)' entry or nothing selected."""
    if not text:
        return u""
    t = u"{0}".format(text).strip()
    if not t or t in (LEAVE_TEMPLATE, DEFAULT_VIEWPORT):
        return u""
    return t


def layout(rows, sheet_w, sheet_h, left, top, gap_x, gap_y, label_h):
    """Where each viewport's CENTRE goes.

    rows: one list per chamber, each a list of (width, height) viewport
    box sizes, all in the same units as the sheet size. The sheet origin
    is its bottom-left corner with y up. The first view's top-left corner
    sits at (left, sheet_h - top); a row runs left to right; a view that
    would pass the right edge wraps to a new line inside its row (unless
    it is the first on the line); each chamber's next row starts below
    the tallest view of the row plus the title allowance and the gap.

    Returns (centres, below): centres mirrors rows with (cx, cy) tuples,
    below counts views whose bottom (with title) falls under the sheet."""
    y_top = sheet_h - top
    centres = []
    below = 0
    for row in rows:
        x = left
        line_h = 0.0
        out_row = []
        for w, h in row:
            if x > left and x + w > sheet_w:
                y_top -= line_h + label_h + gap_y
                x = left
                line_h = 0.0
            out_row.append((x + w * 0.5, y_top - h * 0.5))
            if y_top - h - label_h < 0:
                below += 1
            x += w + gap_x
            if h > line_h:
                line_h = h
        centres.append(out_row)
        y_top -= line_h + label_h + gap_y
    return centres, below


def natural_key(text):
    """Sort key that orders LV1, LV2, LV10 the way people read them."""
    import re as _re
    parts = _re.split(r"(\d+)", u"{0}".format(text or u""))
    key = []
    for part in parts:
        if part.isdigit():
            key.append((1, int(part), u""))
        elif part:
            key.append((2, 0, part.lower()))
    return key


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
