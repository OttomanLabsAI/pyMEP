# -*- coding: utf-8 -*-
"""Shared config and path helpers for pyMEP buttons.

Paths are auto-detected relative to the extension's own folder.

Default `script_folder`:
    <extension root>/conduit_analysis/
Default `export_folder`:
    <extension root>/exports/<revit filename>/

Everything is bundled inside the extension folder, so cloning the repo to
`%APPDATA%\\pyRevit\\Extensions\\pyMEP.extension\\` gives a working setup
with no Settings step required.

User settings (`%APPDATA%\\pyRevit\\pyMEP_settings.json`) override the
auto-detected paths:

  {
    "script_folder":          "",          # override, else auto
    "python_exe":             "python",
    "export_folder_override": ""           # override, else auto
  }
"""

import os
import json

# ---------------------------------------------------------------------------
# PATHS RELATIVE TO THIS FILE
# ---------------------------------------------------------------------------
# This file lives at <ext root>/lib/pymep_config.py, so the extension root
# is two folders up from __file__.
_THIS_FILE   = os.path.abspath(__file__)
_LIB_DIR     = os.path.dirname(_THIS_FILE)
EXT_ROOT     = os.path.dirname(_LIB_DIR)  # pyMEP.extension/
SCRIPTS_DIR  = os.path.join(EXT_ROOT, "conduit_analysis")
EXPORTS_ROOT = os.path.join(EXT_ROOT, "exports")

# User-level settings file (optional overrides). Kept separate from the
# sibling pyMEP extension so the two don't clobber each other's config.
CONFIG_FILE = os.path.join(
    os.environ.get("APPDATA", ""), "pyRevit", "pyMEP_settings.json")
# Older settings file (pre-rename). Read as a fallback so saved settings carry
# over; the next save writes to the new file above.
_OLD_CONFIG_FILE = os.path.join(
    os.environ.get("APPDATA", ""), "pyRevit", "pyMEPv2_settings.json")


def load_settings():
    path = CONFIG_FILE if os.path.exists(CONFIG_FILE) else _OLD_CONFIG_FILE
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_settings(settings):
    d = os.path.dirname(CONFIG_FILE)
    if not os.path.exists(d):
        try: os.makedirs(d)
        except: pass
    with open(CONFIG_FILE, "w") as f:
        json.dump(settings, f, indent=2)


# ---------------------------------------------------------------------------
# SCRIPT (conduit_analysis) FOLDER
# ---------------------------------------------------------------------------
def get_script_folder():
    """conduit_analysis folder. Priority:

    1. explicit user override in settings (`script_folder`),
    2. `<extension root>/conduit_analysis/` if it exists,
    3. empty string (Export Pipework Data will fail with a clear message).
    """
    s = load_settings()
    override = (s.get("script_folder") or "").strip()
    if override and os.path.isdir(override):
        return override
    if os.path.isdir(SCRIPTS_DIR):
        return SCRIPTS_DIR
    return ""


# ---------------------------------------------------------------------------
# EXPORT FOLDER
# ---------------------------------------------------------------------------
def _safe_folder_name(title):
    if not title:
        return "Untitled"
    name = title
    if name.lower().endswith(".rvt"):
        name = name[:-4]
    invalid = '<>:"/\\|?*'
    safe = "".join("_" if ch in invalid else ch for ch in name).strip()
    return safe or "Untitled"


def get_default_export_folder(doc):
    """`<extension root>/exports/<revit filename>/`, auto-created."""
    folder = os.path.join(EXPORTS_ROOT, _safe_folder_name(doc.Title))
    if not os.path.exists(folder):
        try: os.makedirs(folder)
        except: pass
    return folder


def get_export_folder(doc):
    """Return override if set and exists, otherwise the auto folder."""
    s = load_settings()
    override = (s.get("export_folder_override") or "").strip()
    if override and os.path.isdir(override):
        return override
    return get_default_export_folder(doc)


# ---------------------------------------------------------------------------
# PYTHON EXECUTABLE
# ---------------------------------------------------------------------------
def get_python_exe():
    s = load_settings()
    return (s.get("python_exe") or "python").strip()


# ---------------------------------------------------------------------------
# BUILD DUCTS SETTINGS
# ---------------------------------------------------------------------------
# Defaults used when the user hasn't overridden them in Settings. These
# match the RHD template currently in use.
DEFAULT_DUCT_TYPE_NAME   = "RHD_Du_Rectangular"
DEFAULT_DUCT_SYSTEM_NAME = "Supply Air"


def get_duct_type_name():
    """Name of the rectangular Revit DuctType to use when building ducts
    from the duct_centrelines CSV. Falls back to DEFAULT_DUCT_TYPE_NAME."""
    s = load_settings()
    return (s.get("duct_type_name") or DEFAULT_DUCT_TYPE_NAME).strip()


def get_duct_system_type_name():
    """Name of the Revit MechanicalSystemType (MEP system) to assign to
    ducts placed by the Build Ducts button. Falls back to
    DEFAULT_DUCT_SYSTEM_NAME."""
    s = load_settings()
    return (s.get("duct_system_type_name") or DEFAULT_DUCT_SYSTEM_NAME).strip()


# ---------------------------------------------------------------------------
# BUILD PIPES (FROM CSV) SETTINGS
# ---------------------------------------------------------------------------
# Defaults for the Pipes > Build from CSV button. The CSV unit is the unit
# of the start/end XYZ values in the CSV (the example data is in metres).
DEFAULT_PIPE_TYPE_NAME    = "PE SDR11 - Drainage"
DEFAULT_PIPE_SYSTEM_NAME  = "SEWER BATTERY DRAINAGE"
DEFAULT_PIPES_CSV_UNIT    = "m"   # one of: "m", "mm", "ft"
DEFAULT_PIPE_HOST_LEVEL   = "LVL 0.00"
# Survey-to-project offsets, in metres, subtracted from each CSV XYZ before
# placement. Default 0 = pass CSV coordinates through unchanged. Set these
# to your project's survey base point (or to the centroid of your CSV
# coordinates) to bring placements close to the project origin.
DEFAULT_PIPES_X_OFFSET_M  = 0.0
DEFAULT_PIPES_Y_OFFSET_M  = 0.0
DEFAULT_PIPES_Z_OFFSET_M  = 0.0
# True-North rotation around the XY offset point, in DEGREES, applied
# AFTER the XY offsets are subtracted. Positive = CCW. Default 0 = no
# rotation (CSV XY taken as-is). For HEL11-style survey-grid drawings
# the working value is around +/-124.703 deg.
DEFAULT_PIPES_ROTATION_DEG = 0.0
# Post-rotation XY shift in MILLIMETRES, applied AFTER the rotation in
# Revit's coordinate frame. Use this for fine alignment to a project
# reference point - typing +/- values is intuitive because they match
# what you see in the Revit display (which is in mm). Default 0 = no
# additional shift.
DEFAULT_PIPES_POST_X_SHIFT_MM = 0.0
DEFAULT_PIPES_POST_Y_SHIFT_MM = 0.0
# If True, IGNORE the manual XYZ offsets / rotation and instead read the
# document's ActiveProjectLocation.GetTotalTransform() to convert from
# survey/shared coordinates (CSV) to internal coordinates. This is the
# correct mode for any project that has been georeferenced via Acquire
# Coordinates, Specify Coordinates at Point, or by moving the Survey
# Point. Default False so existing manual setups don't change behaviour.
DEFAULT_PIPES_USE_PROJECT_LOCATION = False
# Workset name used when the CSV has no workset column. Empty string =
# leave pipes on the active workset (Revit's default behaviour).
DEFAULT_PIPES_DEFAULT_WORKSET = ""

# Manholes (Place Manholes from CSV) - family and type to instance per row
DEFAULT_MANHOLE_FAMILY_NAME = ""
DEFAULT_MANHOLE_TYPE_NAME   = ""
# Name of the instance parameter on the manhole family that controls
# its overall height. Differs by family - common values: 'Height',
# 'total_height', 'Depth', 'Total Height'.
DEFAULT_MANHOLE_HEIGHT_PARAM = "Height"
# Added to total_height at placement time (mm). Lets one CSV cover multiple
# slab build-ups without regenerating.
DEFAULT_MANHOLE_SLAB_THICKNESS_MM = 0.0

# Drop Pipes (Place Drop Pipes from CSV)
DEFAULT_DROP_PIPE_FAMILY_NAME = "Drop Pipe"
DEFAULT_DROP_PIPE_TYPE_NAME   = "Drop Pipe"
# Family parameter names. dia comes from the CSV's dia_4 column,
# height from z_off_4. Both columns are in csv_unit (m by default);
# the placement code converts to mm before setting the parameter.
DEFAULT_DROP_PIPE_DIA_PARAM    = "DIA"
DEFAULT_DROP_PIPE_HEIGHT_PARAM = "Height"

# Annotate (Annotate Duct Group) - default suffix text appended to the
# generated 'NxM - kNo.D(dia)' label. Overridable in Settings.
DEFAULT_ANNOTATE_SUFFIX = "PVCU DUCTS"

# Annotate Pipes - default perpendicular offset (mm) applied to each
# auto-placed '{D}mm @ 1:{X}' label. The label sits this far away from
# its pipe's midpoint, measured perpendicular to the pipe's XY direction
# (rotated +90 deg CCW after the direction is sign-normalised so parallel
# pipes get labels on the same side).
DEFAULT_ANNOTATE_PIPE_OFFSET_MM = 500.0


# Survey-grid (m) -> project-local (mm) rigid transform.
#
# The same constants used by xlsx_to_pipes_csv.py to convert raw S2CSV
# AutoCAD output into the project-local CSV format. The Place Manholes
# button applies this to the manhole S2CSV before placement, so manhole
# data and pre-converted pipe data end up in the same coordinate frame
# and use the same Settings.
#
# Anchored on BATP12 endpoints from the production CSV; residuals are
# sub-mm. Apply as:
#
#   project_xy_mm = R(rot_deg) . (survey_xy_m * 1000) + translation_mm
#   project_z_mm  = survey_z_m * 1000
#
SURVEY_TO_PROJECT_ROTATION_DEG    = 124.7030585321
SURVEY_TO_PROJECT_TRANSLATION_MM  = (19456384529.838814,
                                     -16348550342.291759)

# ===========================================================================
# LandXML / structures survey-to-internal transform (Place Pipes from
# LandXML, Place Structures). These are the SITE survey origin and rotation,
# read by pymep_landxml_place2.py and pymep_structures_place.py. They are
# subtracted/rotated to bring survey-grid metres into Revit internal feet.
#
# These defaults are overridden by the Settings dialog
# (landxml_off_e_m / landxml_off_n_m / landxml_off_z_m / landxml_rot_deg
# keys in pyMEP_settings.json) so moving between sites needs NO code edit.
# Set them in Settings > Pipes-Coordinates.
#
# Current values: HNU1A. Survey origin from the acquired Civil 3D shared
# site (Project Base Point):
#   E0 = 3,498,151.6589 m   N0 = 5,554,088.8918 m
#   Z0 = 108.200 m (AOD)    True North = 40.36 deg
# Z offset = 0 here is NOT used: structures/pipes are placed at TRUE (AOD)
# elevations; the model base point sits at the site datum so internal Z
# displays as real levels. landxml_off_z_m defaults to 0.0.
#
# ROTATION SIGN IS UNVERIFIED: verify by placing one known structure and
# checking it lands correctly; if the network is mirrored/rotated wrong,
# negate landxml_rot_deg in Settings (40.36 <-> -40.36).
# ===========================================================================
DEFAULT_LANDXML_OFF_E_M  = 3498151.6589
DEFAULT_LANDXML_OFF_N_M  = 5554088.8918
DEFAULT_LANDXML_OFF_Z_M  = 0.0
DEFAULT_LANDXML_ROT_DEG  = 40.36


def get_landxml_survey_transform():
    """(off_e_m, off_n_m, off_z_m, rot_deg) survey-to-internal transform
    for the LandXML pipe / structure placement buttons. Read from the
    Settings dialog; falls back to the DEFAULT_LANDXML_* site constants."""
    s = load_settings()

    def _f(key, default):
        try:
            v = s.get(key)
            return float(v) if v not in (None, "") else default
        except (TypeError, ValueError):
            return default

    return (_f("landxml_off_e_m", DEFAULT_LANDXML_OFF_E_M),
            _f("landxml_off_n_m", DEFAULT_LANDXML_OFF_N_M),
            _f("landxml_off_z_m", DEFAULT_LANDXML_OFF_Z_M),
            _f("landxml_rot_deg", DEFAULT_LANDXML_ROT_DEG))


# AutoCAD layer -> Revit workset map for manholes.
MANHOLE_LAYER_WORKSET_MAP = {
    "_ACM-DR-Ss_50_35_08_30-M-PIPENETWORK_FW-Model 3D Solid":
        "CONTROL - DRAINAGE - FOUL WATER",
    "_ACM-C-OILY WATER PIPE":
        "CONTROL - DRAINAGE - OILY WATER",
    "_ACM-C-SUB SOIL DRAINAGE":
        "CONTROL - DRAINAGE - SUB SOIL",
    "_ACM-C-DRAINAGE RW PIPE":
        "CONTROL - DRAINAGE - RAINWATER",
    "_ACM-DR-Ss_50_35_08_85-M-PIPENETWORK_Pipes_Battery Room":
        "CONTROL - DRAINAGE - BATTERY ROOM",
    "_ACM-DR-Ss_50_35_08_85-M-PIPENETWORK_SW-Model 3D Solid":
        "CONTROL - DRAINAGE - SW+ CP",
}


def get_pipe_type_name():
    """Name of the Revit PipeType to use when building pipes from a CSV.
    Falls back to DEFAULT_PIPE_TYPE_NAME."""
    s = load_settings()
    return (s.get("pipe_type_name") or DEFAULT_PIPE_TYPE_NAME).strip()


def get_pipe_system_type_name():
    """Name of the Revit PipingSystemType to assign to pipes placed by the
    Build from CSV button. Falls back to DEFAULT_PIPE_SYSTEM_NAME."""
    s = load_settings()
    return (s.get("pipe_system_type_name") or DEFAULT_PIPE_SYSTEM_NAME).strip()


def get_pipes_csv_unit():
    """Linear unit of the start/end XYZ values in the pipes CSV. One of
    'm', 'mm', or 'ft'. Falls back to DEFAULT_PIPES_CSV_UNIT."""
    s = load_settings()
    u = (s.get("pipes_csv_unit") or DEFAULT_PIPES_CSV_UNIT).strip().lower()
    if u not in ("m", "mm", "ft"):
        u = DEFAULT_PIPES_CSV_UNIT
    return u


def get_pipe_host_level_name():
    """Name of the Revit Level that all CSV-built pipes are hosted on.
    The pipe's actual end elevations come from the CSV's Z values; the
    host level is just the reference Revit attaches the pipe to. Falls
    back to DEFAULT_PIPE_HOST_LEVEL."""
    s = load_settings()
    return (s.get("pipe_host_level") or DEFAULT_PIPE_HOST_LEVEL).strip()


def get_pipes_xyz_offset_m():
    """(x_off, y_off, z_off) survey-to-project offsets in metres,
    subtracted from each CSV XYZ value before placement. Z is interpreted
    as elevation ABOVE the host level after the offset is subtracted."""
    s = load_settings()
    def _f(key, default):
        try:
            v = s.get(key)
            return float(v) if v not in (None, "") else default
        except (TypeError, ValueError):
            return default
    return (_f("pipes_x_offset_m", DEFAULT_PIPES_X_OFFSET_M),
            _f("pipes_y_offset_m", DEFAULT_PIPES_Y_OFFSET_M),
            _f("pipes_z_offset_m", DEFAULT_PIPES_Z_OFFSET_M))


def get_pipes_default_workset():
    """Workset name to assign all pipes to when the CSV has no workset
    column and ws_filter is None. Empty -> leave pipes on the active
    workset. Falls back to DEFAULT_PIPES_DEFAULT_WORKSET."""
    s = load_settings()
    return (s.get("pipes_default_workset") or DEFAULT_PIPES_DEFAULT_WORKSET).strip()


def get_manhole_family_name():
    s = load_settings()
    return (s.get("manhole_family_name") or DEFAULT_MANHOLE_FAMILY_NAME).strip()


def get_manhole_type_name():
    s = load_settings()
    return (s.get("manhole_type_name") or DEFAULT_MANHOLE_TYPE_NAME).strip()


def get_manhole_height_param():
    """Name of the instance parameter on the manhole family that holds
    the overall height. Falls back to DEFAULT_MANHOLE_HEIGHT_PARAM."""
    s = load_settings()
    return (s.get("manhole_height_param") or DEFAULT_MANHOLE_HEIGHT_PARAM).strip()


def get_manhole_slab_thickness_mm():
    """Slab thickness in mm to add to each row's total_height at placement
    time. Falls back to DEFAULT_MANHOLE_SLAB_THICKNESS_MM (0)."""
    s = load_settings()
    try:
        v = s.get("manhole_slab_thickness_mm")
        return float(v) if v not in (None, "") else DEFAULT_MANHOLE_SLAB_THICKNESS_MM
    except (TypeError, ValueError):
        return DEFAULT_MANHOLE_SLAB_THICKNESS_MM


# ---- Drop Pipes ----------------------------------------------------------

def get_drop_pipe_family_name():
    s = load_settings()
    return (s.get("drop_pipe_family_name") or DEFAULT_DROP_PIPE_FAMILY_NAME).strip()


def get_drop_pipe_type_name():
    s = load_settings()
    return (s.get("drop_pipe_type_name") or DEFAULT_DROP_PIPE_TYPE_NAME).strip()


def get_drop_pipe_dia_param():
    s = load_settings()
    return (s.get("drop_pipe_dia_param") or DEFAULT_DROP_PIPE_DIA_PARAM).strip()


def get_drop_pipe_height_param():
    s = load_settings()
    return (s.get("drop_pipe_height_param") or DEFAULT_DROP_PIPE_HEIGHT_PARAM).strip()


def get_pipes_rotation_deg():
    """True-North rotation in DEGREES applied to (csv_xy - offset_xy)
    before placement. Positive = CCW. Falls back to
    DEFAULT_PIPES_ROTATION_DEG."""
    s = load_settings()
    try:
        v = s.get("pipes_rotation_deg")
        return float(v) if v not in (None, "") else DEFAULT_PIPES_ROTATION_DEG
    except (TypeError, ValueError):
        return DEFAULT_PIPES_ROTATION_DEG


def get_pipes_post_shift_mm():
    """Post-rotation XY shift in MILLIMETRES, applied in Revit's frame
    AFTER rotation. Returns (x_mm, y_mm). Falls back to defaults."""
    s = load_settings()
    def _f(key, default):
        try:
            v = s.get(key)
            return float(v) if v not in (None, "") else default
        except (TypeError, ValueError):
            return default
    return (_f("pipes_post_x_shift_mm", DEFAULT_PIPES_POST_X_SHIFT_MM),
            _f("pipes_post_y_shift_mm", DEFAULT_PIPES_POST_Y_SHIFT_MM))


def get_pipes_use_project_location():
    """If True, the build reads the document's ActiveProjectLocation
    transform to convert CSV survey/shared coordinates into internal
    coordinates, ignoring all manual XYZ offsets and rotation. Falls
    back to DEFAULT_PIPES_USE_PROJECT_LOCATION."""
    s = load_settings()
    v = s.get("pipes_use_project_location")
    if v is None:
        return DEFAULT_PIPES_USE_PROJECT_LOCATION
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("true", "yes", "1", "on", "y")
    return bool(v)


# ---- Annotate ------------------------------------------------------------

def get_annotate_suffix():
    """Suffix text appended below the 'NxM - kNo.D(dia)' label produced by the
    Annotate Duct Group button (e.g. 'PVCU DUCTS'). Falls back to
    DEFAULT_ANNOTATE_SUFFIX."""
    s = load_settings()
    v = s.get("annotate_suffix")
    if v is None or str(v).strip() == "":
        return DEFAULT_ANNOTATE_SUFFIX
    return str(v).strip()


def get_annotate_pipe_offset_mm():
    """Perpendicular offset (mm) for auto-placed Annotate Pipes labels.
    Falls back to DEFAULT_ANNOTATE_PIPE_OFFSET_MM. Negative or
    non-numeric values fall back to the default."""
    s = load_settings()
    v = s.get("annotate_pipe_offset_mm")
    if v is None:
        return DEFAULT_ANNOTATE_PIPE_OFFSET_MM
    try:
        f = float(v)
        return f if f >= 0 else DEFAULT_ANNOTATE_PIPE_OFFSET_MM
    except (TypeError, ValueError):
        return DEFAULT_ANNOTATE_PIPE_OFFSET_MM


# ---------------------------------------------------------------------------
# LANDXML -> REVIT SETTINGS
# ---------------------------------------------------------------------------
# Name of the Revit pipe Segment that 'Create Pipe Sizes' adds the LandXML
# diameters to. Leave blank to be prompted to pick from the project's
# segments at run time (recommended - segment names vary by template).
DEFAULT_LANDXML_SEGMENT_NAME = ""
# Pipe type + system type used by 'Model Pipes' for the placed pipes.
# These reuse the existing pipe-builder defaults so one Settings entry
# drives both the CSV builder and the LandXML builder.


def get_landxml_segment_name():
    """Name of the Revit PipeSegment that 'Create Pipe Sizes' adds the
    LandXML circular diameters to. Empty -> prompt at run time."""
    s = load_settings()
    return (s.get("landxml_segment_name") or DEFAULT_LANDXML_SEGMENT_NAME).strip()


def get_landxml_network_workset_map():
    """Return the saved {network_name: workset_name} map used by 'Model
    Pipes' to pre-fill the per-network workset mapping. Empty dict if
    none saved yet. Stored in settings under 'landxml_network_workset_map'.
    """
    s = load_settings()
    m = s.get("landxml_network_workset_map")
    return dict(m) if isinstance(m, dict) else {}


def save_landxml_network_workset_map(mapping):
    """Persist the {network: workset} map so the next run pre-fills it."""
    s = load_settings()
    s["landxml_network_workset_map"] = dict(mapping or {})
    save_settings(s)


# ---------------------------------------------------------------------------
# SECTION DIMS - chamber reference-plane dimension pairs
# ---------------------------------------------------------------------------
# The Dimension Section button dimensions the chamber between named reference
# planes in the chamber family. Each pair drives one dimension:
#   label    : human label for the report (e.g. "External Width")
#   plane_a  : reference-plane NAME in the family (e.g. "EXT_LEFT")
#   plane_b  : the opposite plane name (e.g. "EXT_RIGHT")
#   axis     : "width"  -> dimension line runs along the section RightDirection
#              "height" -> dimension line runs along the section UpDirection
#
# The planes must exist in the family, be named exactly as below, and have
# "Is Reference" set to a real reference (Strong Reference recommended) so the
# API can fetch them via FamilyInstance.GetReferenceByName.
#
# Edit the names/pairs in Settings > Section Dims (no code change needed).
DEFAULT_CHAMBER_DIM_PAIRS = [
    # Width dims (horizontal). Ordered innermost -> outermost so they nest:
    # wall thicknesses closest to the box, internal next, external furthest.
    {"label": "Left Wall",       "plane_a": "EXT_LEFT",
     "plane_b": "EXT_INT_LEFT",  "axis": "width"},
    {"label": "Right Wall",      "plane_a": "EXT_RIGHT",
     "plane_b": "EXT_INT_RIGHT", "axis": "width"},
    {"label": "Internal Width",  "plane_a": "EXT_INT_LEFT",
     "plane_b": "EXT_INT_RIGHT", "axis": "width"},
    {"label": "External Width",  "plane_a": "EXT_LEFT",
     "plane_b": "EXT_RIGHT",     "axis": "width"},
    # Height dims (vertical).
    {"label": "Overall Height",  "plane_a": "EXT_TOP_ENT",
     "plane_b": "EXT_INT_BASE",  "axis": "height"},
    {"label": "Entrance Height", "plane_a": "EXT_TOP_ENT",
     "plane_b": "EXT_TOP",       "axis": "height"},
]


def get_chamber_dim_pairs():
    """Return the list of chamber reference-plane dimension pairs used by the
    Dimension Section button. Each item is a dict with keys label, plane_a,
    plane_b, axis. Falls back to DEFAULT_CHAMBER_DIM_PAIRS when unset."""
    s = load_settings()
    pairs = s.get("chamber_dim_pairs")
    if not isinstance(pairs, list) or not pairs:
        return [dict(p) for p in DEFAULT_CHAMBER_DIM_PAIRS]
    out_pairs = []
    for p in pairs:
        if not isinstance(p, dict):
            continue
        pa = str(p.get("plane_a", "")).strip()
        pb = str(p.get("plane_b", "")).strip()
        if not pa or not pb:
            continue
        axis = str(p.get("axis", "width")).strip().lower()
        if axis not in ("width", "height"):
            axis = "width"
        out_pairs.append({
            "label": str(p.get("label", pa + " / " + pb)).strip(),
            "plane_a": pa,
            "plane_b": pb,
            "axis": axis,
        })
    return out_pairs if out_pairs else [dict(p) for p in DEFAULT_CHAMBER_DIM_PAIRS]


def save_chamber_dim_pairs(pairs):
    """Persist the chamber reference-plane dimension pairs."""
    s = load_settings()
    clean = []
    for p in (pairs or []):
        if not isinstance(p, dict):
            continue
        clean.append({
            "label": str(p.get("label", "")).strip(),
            "plane_a": str(p.get("plane_a", "")).strip(),
            "plane_b": str(p.get("plane_b", "")).strip(),
            "axis": str(p.get("axis", "width")).strip().lower(),
        })
    s["chamber_dim_pairs"] = clean
    save_settings(s)
