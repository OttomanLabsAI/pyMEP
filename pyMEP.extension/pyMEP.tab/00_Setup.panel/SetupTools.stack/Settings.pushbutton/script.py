# -*- coding: utf-8 -*-
"""pyMEP Settings

Configure the conduit_analysis folder, Python executable, and optional
override for the export folder.

Default export folder: <extension root>/exports/<revit filename>/  (auto-created)
"""

__title__ = "Settings"
__author__ = "Glent Group"

import os
from pyrevit import forms, revit

from pymep_config import (
    load_settings, save_settings,
    get_default_export_folder, get_export_folder,
    DEFAULT_DUCT_TYPE_NAME, DEFAULT_DUCT_SYSTEM_NAME,
    DEFAULT_PIPE_TYPE_NAME, DEFAULT_PIPE_SYSTEM_NAME,
    DEFAULT_PIPE_HOST_LEVEL,
    DEFAULT_ANNOTATE_SUFFIX, DEFAULT_ANNOTATE_PIPE_OFFSET_MM,
    DEFAULT_LANDXML_OFF_E_M, DEFAULT_LANDXML_OFF_N_M,
    DEFAULT_LANDXML_OFF_Z_M, DEFAULT_LANDXML_ROT_DEG,
    get_chamber_dim_pairs, save_chamber_dim_pairs,
)

doc = revit.doc


def _short_or_unset(val, default=None):
    """Format a setting value for the compact summary, with sensible
    fallback when not set."""
    if val is None or val == "":
        return "({})".format(default if default else "not set")
    if isinstance(val, float):
        return "{:.4g}".format(val)
    return str(val)


def _read_state():
    """Re-read settings into a dict every time we redraw the menu, so
    edits made in submenus appear on return."""
    s2 = load_settings()
    state = {
        "script_folder":        s2.get("script_folder", ""),
        "python_exe":           s2.get("python_exe", ""),
        "export_override":      s2.get("export_folder_override", ""),
        "active_export":        get_export_folder(doc),
        "auto_folder":          get_default_export_folder(doc),

        "duct_type":            s2.get("duct_type_name", "")           or DEFAULT_DUCT_TYPE_NAME,
        "duct_system":          s2.get("duct_system_type_name", "")    or DEFAULT_DUCT_SYSTEM_NAME,

        "pipe_type":            s2.get("pipe_type_name", "")           or DEFAULT_PIPE_TYPE_NAME,
        "pipe_system":          s2.get("pipe_system_type_name", "")    or DEFAULT_PIPE_SYSTEM_NAME,
        "pipe_host_level":      s2.get("pipe_host_level", "")          or DEFAULT_PIPE_HOST_LEVEL,
        "auto_close":           False,
        "segment":              (s2.get("landxml_segment_name") or "").strip(),
    }
    _ac = s2.get("auto_close_output")
    if isinstance(_ac, str):
        _ac = _ac.strip().lower() in ("true", "yes", "1", "on", "y")
    state["auto_close"] = bool(_ac)
    def _lf(key, dflt):
        try:
            v = s2.get(key)
            return float(v) if v not in (None, "") else dflt
        except (TypeError, ValueError):
            return dflt
    state["lx_e"]   = _lf("landxml_off_e_m", DEFAULT_LANDXML_OFF_E_M)
    state["lx_n"]   = _lf("landxml_off_n_m", DEFAULT_LANDXML_OFF_N_M)
    state["lx_z"]   = _lf("landxml_off_z_m", DEFAULT_LANDXML_OFF_Z_M)
    state["lx_rot"] = _lf("landxml_rot_deg", DEFAULT_LANDXML_ROT_DEG)

    state["annotate_suffix"]        = (s2.get("annotate_suffix") or "").strip() or DEFAULT_ANNOTATE_SUFFIX
    _pipe_off = s2.get("annotate_pipe_offset_mm")
    try:
        state["annotate_pipe_offset_mm"] = float(_pipe_off) if _pipe_off is not None else DEFAULT_ANNOTATE_PIPE_OFFSET_MM
    except (TypeError, ValueError):
        state["annotate_pipe_offset_mm"] = DEFAULT_ANNOTATE_PIPE_OFFSET_MM
    try:
        state["chamber_dim_pair_count"] = len(get_chamber_dim_pairs())
    except Exception:
        state["chamber_dim_pair_count"] = 0
    return state


def _category_summary(state):
    """One-line headline per category, shown next to the category name."""
    return {
        "General":              "{} | {}".format(
                                    _short_or_unset(state["script_folder"], "no script folder"),
                                    _short_or_unset(state["python_exe"], "python on PATH")),
        "Ducts":                "{}  /  {}".format(
                                    _short_or_unset(state["duct_type"]),
                                    _short_or_unset(state["duct_system"])),
        "Pipes":                "{}  /  {}  /  level {}  |  LandXML E0 {:.0f} N0 {:.0f} rot {:.2f}".format(
                                    _short_or_unset(state["pipe_type"]),
                                    _short_or_unset(state["pipe_system"]),
                                    state["pipe_host_level"],
                                    state["lx_e"], state["lx_n"], state["lx_rot"]),
        "Annotate":             "suffix `{}`  /  pipe offset {:.0f} mm".format(
                                    _short_or_unset(state["annotate_suffix"]),
                                    state["annotate_pipe_offset_mm"]),
        "Section Dims":         "{} chamber dim pair(s)".format(
                                    state.get("chamber_dim_pair_count", 0)),
    }


def _detail_summary(state, category):
    """Full info pane shown for a category submenu."""
    if category == "General":
        return (
            "conduit_analysis folder:\n  {}\n\n"
            "Python executable:\n  {}\n\n"
            "Default export folder:\n  {}\n\n"
            "Export folder override:\n  {}\n\n"
            "Active export folder:\n  {}\n\n"
            "Auto-close output window after each command:\n  {}".format(
                state["script_folder"] or "(not set)",
                state["python_exe"] or "python (PATH)",
                state["auto_folder"],
                state["export_override"] or "(none - uses default)",
                state["active_export"],
                "ON" if state["auto_close"] else "OFF"))
    if category == "Ducts":
        return (
            "Duct type (Build Ducts):\n  {}\n\n"
            "Duct MEP system type (Build Ducts):\n  {}".format(
                state["duct_type"], state["duct_system"]))
    if category == "Pipes":
        return (
            "Pipe type (Place Pipes):\n  {}\n\n"
            "Pipe system type (Place Pipes):\n  {}\n\n"
            "Pipe host level (Place Pipes):\n  {}\n\n"
            "Pipe segment for sizes (Create Pipe Sizes / Place Pipes):\n  {}\n\n"
            "LandXML survey origin (Place Pipes / Boxes / Cylinders):\n"
            "  E0={:.4f}  N0={:.4f}  Z0={:.4f} m  rot={:.4f} deg".format(
                state["pipe_type"], state["pipe_system"],
                state["pipe_host_level"],
                state["segment"] or "(not set - picker at run time)",
                state["lx_e"], state["lx_n"], state["lx_z"], state["lx_rot"]))
    if category == "Annotate":
        return (
            "Suffix text (Annotate Duct Group):\n  {}\n\n"
            "Appended on the second line of the label produced by\n"
            "Annotate > Annotate Ducts. The first line is generated\n"
            "from the selection (e.g. '3x1 - 3No.200\u00d8').\n\n"
            "Pipe annotation offset (Annotate Pipes):\n  {:.0f} mm\n\n"
            "Perpendicular distance the auto-placed '{{D}}mm @ 1:{{X}}'\n"
            "label sits away from each pipe's midpoint, in model mm.".format(
                state["annotate_suffix"],
                state["annotate_pipe_offset_mm"]))
    if category == "Section Dims":
        pairs = get_chamber_dim_pairs()
        if not pairs:
            return ("Chamber dimension pairs (Dimension Section):\n"
                    "  (none configured)")
        lines = ["Chamber dimension pairs (Dimension Section):", ""]
        for i, p in enumerate(pairs):
            lines.append("  {0}. {1}".format(i + 1, p["label"]))
            lines.append("      {0} <-> {1}   ({2})".format(
                p["plane_a"], p["plane_b"], p["axis"]))
        lines.append("")
        lines.append("These are reference-plane NAMES in the chamber family.")
        lines.append("Set each plane's 'Is Reference' to Strong Reference.")
        return "\n".join(lines)
    return ""


CATEGORY_ITEMS = {
    "General": [
        "Set conduit_analysis folder",
        "Set Python executable",
        "Set export folder override",
        "Clear export folder override",
        "Open active export folder",
        "Toggle output window auto-close",
        "<- Back",
    ],
    "Ducts": [
        "Set duct type name",
        "Set duct MEP system type name",
        "<- Back",
    ],
    "Pipes": [
        "Set pipe type name",
        "Set pipe system type name",
        "Set pipe host level name",
        "Set pipe segment name (sizes)",
        "Set LandXML survey origin (E/N/Z/rot)",
        "<- Back",
    ],
    "Annotate": [
        "Set annotate suffix text",
        "Set pipe annotation offset (mm)",
        "<- Back",
    ],
    "Section Dims": [
        "List chamber dimension pairs",
        "Add a chamber dimension pair",
        "Edit a chamber dimension pair",
        "Remove a chamber dimension pair",
        "Reset chamber dimension pairs to default",
        "<- Back",
    ],
}

CATEGORY_ORDER = ["General", "Ducts", "Pipes", "Annotate",
                  "Section Dims"]


def handle_choice(choice):
    """Run the editor for `choice`. Reloads settings fresh so a save here
    never clobbers edits made elsewhere in the same session (e.g. the
    chamber-dim-pair editors save through their own load_settings())."""
    s = load_settings()

    if choice == "Set conduit_analysis folder":
        folder = forms.pick_folder(
            title="Pick the conduit_analysis folder (contains run_analysis.py)")
        if folder:
            s["script_folder"] = folder
            save_settings(s)

    elif choice == "Set Python executable":
        txt = forms.ask_for_string(
            prompt="Full path to python.exe, or just 'python' if on PATH:",
            default=s.get("python_exe") or "python",
            title="Python executable")
        if txt is not None:
            s["python_exe"] = txt.strip()
            save_settings(s)

    elif choice == "Set export folder override":
        folder = forms.pick_folder(
            title="Pick an export folder (overrides the default)")
        if folder:
            s["export_folder_override"] = folder
            save_settings(s)

    elif choice == "Clear export folder override":
        s["export_folder_override"] = ""
        save_settings(s)

    elif choice == "Set duct type name":
        txt = forms.ask_for_string(
            prompt="Name of the rectangular Revit duct type to use for\n"
                   "Build Ducts (e.g. 'Mitred Elbows / Taps').",
            default=s.get("duct_type_name") or "",
            title="Duct type name")
        if txt is not None:
            s["duct_type_name"] = txt.strip()
            save_settings(s)

    elif choice == "Set duct MEP system type name":
        txt = forms.ask_for_string(
            prompt="Name of the Revit MEP system type to assign to ducts\n"
                   "(e.g. 'Supply Air', 'Mechanical Return Air').",
            default=s.get("duct_system_type_name") or "",
            title="Duct MEP system type name")
        if txt is not None:
            s["duct_system_type_name"] = txt.strip()
            save_settings(s)

    elif choice == "Set pipe type name":
        txt = forms.ask_for_string(
            prompt="Name of the Revit pipe type used by\n"
                   "Place Pipes (e.g. 'Standard').",
            default=s.get("pipe_type_name") or "",
            title="Pipe type name")
        if txt is not None:
            s["pipe_type_name"] = txt.strip()
            save_settings(s)

    elif choice == "Set pipe system type name":
        txt = forms.ask_for_string(
            prompt="Name of the Revit piping system type to assign to pipes\n"
                   "(e.g. 'Domestic Cold Water', 'Sanitary').",
            default=s.get("pipe_system_type_name") or "",
            title="Pipe system type name")
        if txt is not None:
            s["pipe_system_type_name"] = txt.strip()
            save_settings(s)

    elif choice == "Set pipe host level name":
        txt = forms.ask_for_string(
            prompt="Name of the Revit Level to host placed pipes on.\n"
                   "Pipe end elevations still come from the export's Z values;\n"
                   "the host level is just Revit's reference (e.g. 'LVL 0.00').",
            default=s.get("pipe_host_level") or "",
            title="Pipe host level name")
        if txt is not None:
            s["pipe_host_level"] = txt.strip()
            save_settings(s)

    elif choice == "Set pipe segment name (sizes)":
        txt = forms.ask_for_string(
            prompt="Name of the Revit pipe Segment that Create Pipe Sizes\n"
                   "and Place Pipes add the export's circular sizes to.\n"
                   "Leave blank to be prompted at run time.",
            default=s.get("landxml_segment_name") or "",
            title="Pipe segment name")
        if txt is not None:
            s["landxml_segment_name"] = txt.strip()
            save_settings(s)

    elif choice == "Set LandXML survey origin (E/N/Z/rot)":
        try:
            from pymep_config import get_landxml_survey_transform
            ce, cn, cz, crot = get_landxml_survey_transform()
        except Exception:
            ce, cn, cz, crot = (float(s.get("landxml_off_e_m", 0.0) or 0.0),
                                float(s.get("landxml_off_n_m", 0.0) or 0.0),
                                float(s.get("landxml_off_z_m", 0.0) or 0.0),
                                float(s.get("landxml_rot_deg", 0.0) or 0.0))
        default_str = "{:.4f}, {:.4f}, {:.4f}, {:.4f}".format(ce, cn, cz, crot)
        txt = forms.ask_for_string(
            prompt="Survey origin + rotation for the Place Pipes / Place\n"
                   "Boxes / Cylinders buttons. Four comma-separated values:\n"
                   "  E0, N0, Z0, rotation\n\n"
                   "  E0, N0 = the model's Project Base Point survey easting /\n"
                   "           northing in METRES (Manage > Coordinates, or the\n"
                   "           PBP tag E/W and N/S). Subtracted from each\n"
                   "           LandXML point before rotation.\n"
                   "  Z0     = base elevation in metres to subtract (0 keeps\n"
                   "           absolute AOD elevations - the usual case).\n"
                   "  rot    = Angle to True North in DEGREES.\n\n"
                   "Example (HNU1A):\n"
                   "  3498151.6589, 5554088.8918, 0, 40.36\n\n"
                   "If the placed network is mirrored / rotated the wrong way,\n"
                   "negate the rotation (e.g. 40.36 -> -40.36) and re-run.",
            default=default_str,
            title="LandXML survey origin (E/N/Z/rot)")
        if txt is not None:
            try:
                parts = [p.strip() for p in txt.split(",")]
                if len(parts) != 4:
                    raise ValueError("need four comma-separated numbers: "
                                     "E0, N0, Z0, rot")
                ev, nv, zv, rv = (float(parts[0]), float(parts[1]),
                                  float(parts[2]), float(parts[3]))
                s["landxml_off_e_m"] = ev
                s["landxml_off_n_m"] = nv
                s["landxml_off_z_m"] = zv
                s["landxml_rot_deg"] = rv
                save_settings(s)
            except Exception as ex:
                forms.alert("Could not parse survey origin:\n\n{}".format(ex))

    elif choice == "Set annotate suffix text":
        cur = s.get("annotate_suffix", "")
        txt = forms.ask_for_string(
            prompt="Suffix text appended on the second line of the\n"
                   "duct-group label produced by Annotate > Annotate Ducts.\n\n"
                   "Example: 'PVCU DUCTS', 'HDPE DUCTS', 'LV CONDUITS'.\n\n"
                   "The first line is generated from the selection\n"
                   "(e.g. '3x1 - 3No.200\u00d8').",
            default=cur or "PVCU DUCTS",
            title="Annotate suffix text")
        if txt is not None:
            s["annotate_suffix"] = txt.strip()
            save_settings(s)

    elif choice == "Set pipe annotation offset (mm)":
        cur = s.get("annotate_pipe_offset_mm", DEFAULT_ANNOTATE_PIPE_OFFSET_MM)
        try:
            cur = float(cur)
        except (TypeError, ValueError):
            cur = DEFAULT_ANNOTATE_PIPE_OFFSET_MM
        txt = forms.ask_for_string(
            prompt="Perpendicular offset, in model mm, for each auto-placed\n"
                   "'{D}mm @ 1:{X}' label produced by Annotate > Annotate Pipes.\n\n"
                   "Default: 500. Larger values push the label further from\n"
                   "the pipe. The leader still draws back to the pipe midpoint.",
            default="{:g}".format(cur),
            title="Pipe annotation offset (mm)")
        if txt is not None:
            try:
                v = float(txt.strip())
                if v >= 0:
                    s["annotate_pipe_offset_mm"] = v
                    save_settings(s)
            except (TypeError, ValueError):
                pass

    elif choice == "List chamber dimension pairs":
        pairs = get_chamber_dim_pairs()
        if not pairs:
            forms.alert("No chamber dimension pairs configured.")
        else:
            lines = []
            for i, p in enumerate(pairs):
                lines.append("{0}. {1}: {2} <-> {3} ({4})".format(
                    i + 1, p["label"], p["plane_a"], p["plane_b"], p["axis"]))
            forms.alert("Chamber dimension pairs:\n\n" + "\n".join(lines))

    elif choice == "Add a chamber dimension pair":
        label = forms.ask_for_string(
            prompt="Label for this dimension (e.g. 'External Width').",
            default="External Width", title="Pair label")
        if label:
            pa = forms.ask_for_string(
                prompt="First reference-plane NAME in the chamber family\n"
                       "(e.g. EXT_LEFT). Must match the family exactly.",
                default="EXT_LEFT", title="Plane A name")
            if pa:
                pb = forms.ask_for_string(
                    prompt="Opposite reference-plane NAME (e.g. EXT_RIGHT).",
                    default="EXT_RIGHT", title="Plane B name")
                if pb:
                    axis = forms.SelectFromList.show(
                        ["width", "height"],
                        title="Axis: width (horizontal) or height (vertical)?",
                        button_name="Use this axis")
                    if axis:
                        pairs = get_chamber_dim_pairs()
                        pairs.append({"label": label.strip(),
                                      "plane_a": pa.strip(),
                                      "plane_b": pb.strip(),
                                      "axis": axis})
                        save_chamber_dim_pairs(pairs)

    elif choice == "Edit a chamber dimension pair":
        pairs = get_chamber_dim_pairs()
        if not pairs:
            forms.alert("No pairs to edit. Add one first.")
        else:
            labels = ["{0}. {1} ({2} <-> {3})".format(
                i + 1, p["label"], p["plane_a"], p["plane_b"])
                for i, p in enumerate(pairs)]
            pick = forms.SelectFromList.show(
                labels, title="Which pair to edit?", button_name="Edit")
            if pick:
                idx = labels.index(pick)
                p = pairs[idx]
                label = forms.ask_for_string(
                    prompt="Label.", default=p["label"], title="Pair label")
                if label is not None:
                    pa = forms.ask_for_string(
                        prompt="Plane A name.", default=p["plane_a"],
                        title="Plane A name")
                    pb = forms.ask_for_string(
                        prompt="Plane B name.", default=p["plane_b"],
                        title="Plane B name")
                    axis = forms.SelectFromList.show(
                        ["width", "height"],
                        title="Axis (current: {0})".format(p["axis"]),
                        button_name="Use this axis")
                    pairs[idx] = {
                        "label": (label or p["label"]).strip(),
                        "plane_a": (pa or p["plane_a"]).strip(),
                        "plane_b": (pb or p["plane_b"]).strip(),
                        "axis": axis or p["axis"],
                    }
                    save_chamber_dim_pairs(pairs)

    elif choice == "Remove a chamber dimension pair":
        pairs = get_chamber_dim_pairs()
        if not pairs:
            forms.alert("No pairs to remove.")
        else:
            labels = ["{0}. {1} ({2} <-> {3})".format(
                i + 1, p["label"], p["plane_a"], p["plane_b"])
                for i, p in enumerate(pairs)]
            pick = forms.SelectFromList.show(
                labels, title="Which pair to remove?", button_name="Remove")
            if pick:
                idx = labels.index(pick)
                del pairs[idx]
                save_chamber_dim_pairs(pairs)

    elif choice == "Reset chamber dimension pairs to default":
        if forms.alert("Reset chamber dimension pairs to the default "
                       "EXT_LEFT/RIGHT + EXT_TOP/BOT?", yes=True, no=True):
            # Saving an empty list makes the getter fall back to defaults.
            save_chamber_dim_pairs([])

    elif choice == "Toggle output window auto-close":
        cur = s.get("auto_close_output")
        if isinstance(cur, str):
            cur = cur.strip().lower() in ("true", "yes", "1", "on", "y")
        new_val = not bool(cur)
        s["auto_close_output"] = new_val
        save_settings(s)
        forms.alert(
            "Output window auto-close is now: {}\n\n{}".format(
                "ON" if new_val else "OFF",
                "Each pyMEP command closes its output window when it "
                "finishes. Windows with an error report always stay "
                "open." if new_val else
                "Output windows stay open after each command (the "
                "default)."))

    elif choice == "Open active export folder":
        path = get_export_folder(doc)
        if path and os.path.isdir(path):
            os.startfile(path)
        else:
            forms.alert("Folder does not exist:\n{}".format(path))


# ---------------------------------------------------------------------------
# Two-level dialog: category browser -> settings within category.
# ---------------------------------------------------------------------------
while True:
    state    = _read_state()
    cat_blurbs = _category_summary(state)

    # Decorate each category line with its compact summary so the user
    # can see relevant values at a glance.
    items = []
    for cat in CATEGORY_ORDER:
        items.append("{}    -    {}".format(cat, cat_blurbs[cat]))
    items.append("Close")

    cat_choice = forms.SelectFromList.show(
        items,
        title="pyMEP Settings",
        button_name="Open",
        multiselect=False,
        info="Pick a category. Each row shows current values at a glance.")

    if not cat_choice or cat_choice == "Close":
        break

    # Strip the suffix back off to recover the bare category name.
    chosen_cat = cat_choice.split("    -    ", 1)[0].strip()
    if chosen_cat not in CATEGORY_ITEMS:
        continue

    # Inner loop: stays inside the picked category until "<- Back".
    while True:
        state = _read_state()
        info  = _detail_summary(state, chosen_cat)
        item_choice = forms.SelectFromList.show(
            CATEGORY_ITEMS[chosen_cat],
            title="pyMEP Settings - {}".format(chosen_cat),
            button_name="Do it",
            multiselect=False,
            info=info)
        if not item_choice or item_choice == "<- Back":
            break
        handle_choice(item_choice)
