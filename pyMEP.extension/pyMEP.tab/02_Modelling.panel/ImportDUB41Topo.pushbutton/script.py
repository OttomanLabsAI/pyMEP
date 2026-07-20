# -*- coding: utf-8 -*-
"""Create a Revit Toposolid from DUB41_toposolid_data.json (Composite EG+FG).

Coordinates in the JSON are world ITM (metres). This script converts them
shared -> internal via the model's active Project Location, so SHARED
COORDINATES MUST BE ESTABLISHED in this model before running (survey point
at the correct ITM position). Revit 2024+. Run from pyRevit or RPS.
"""
import json, math, codecs
from pyrevit import revit, DB, forms

# ---------------- settings ----------------
JSON_PATH = forms.pick_file(
    file_ext="json",
    title="Pick the toposolid data JSON (e.g. DUB41_toposolid_data.json)")
if not JSON_PATH:
    forms.alert("No JSON file picked.", exitscript=True)
LEVEL_NAME = None            # None = lowest level in the model
TYPE_NAME = None             # None = first ToposolidType found
MAX_INTERIOR_POINTS = None   # e.g. 8000 to thin for a lighter element; None = all 25,303
MIN_HOLE_AREA_M2 = None      # None = fill all holes (recommended); e.g. 5.0 keeps the two real-ish voids
INCLUDE_BOUNDARY_POINTS = True  # boundary verts as shape points so the rim gets true levels
# ------------------------------------------

M2FT = 1.0 / 0.3048
doc = revit.doc
data = json.load(codecs.open(JSON_PATH, "r", "utf-8"))

# --- shared (survey) -> internal transform, direction self-verified ---
pl = doc.ActiveProjectLocation
pp = pl.GetProjectPosition(DB.XYZ.Zero)  # shared coords of internal origin
if abs(pp.EastWest) < 1e-6 and abs(pp.NorthSouth) < 1e-6:
    # No shared coordinates: offer to drop the site centre on the model's
    # internal origin instead (true elevations kept) - same rescue the
    # pipe / structure placers offer. Cancel keeps the old hard stop.
    _xs = [p[0] for p in data["outer_loop"]]
    _ys = [p[1] for p in data["outer_loop"]]
    _cE = (min(_xs) + max(_xs)) / 2.0
    _cN = (min(_ys) + max(_ys)) / 2.0
    if forms.alert(
            "Shared coordinates don't look established in this model "
            "(survey position of internal origin is 0,0), so the ITM "
            "coordinates can't be mapped to their true position.\n\n"
            "I can place the surface at the model's INTERNAL ORIGIN "
            "instead: the site centre (E {:.1f}, N {:.1f}) lands at 0,0 "
            "and true elevations are kept.\n\n"
            "For a correctly georeferenced surface: Manage > Coordinates "
            "> Specify Coordinates at Point (or Acquire from an ITM "
            "link), then re-run.".format(_cE, _cN),
            title="No shared coordinates",
            options=["Place at internal origin",
                     "Cancel"]) != "Place at internal origin":
        forms.alert("Cancelled - nothing was created.", exitscript=True)

    def to_internal(p):  # p = [E, N, Z] in metres, site centre -> 0,0
        return DB.XYZ((p[0] - _cE) * M2FT, (p[1] - _cN) * M2FT,
                      p[2] * M2FT)
else:
    tt = pl.GetTotalTransform()
    o = tt.Origin
    fwd_is_int_to_shared = (abs(o.X - pp.EastWest) < 1e-4 and
                            abs(o.Y - pp.NorthSouth) < 1e-4)
    sh2int = tt.Inverse if fwd_is_int_to_shared else tt

    def to_internal(p):  # p = [E, N, Z] in metres
        return sh2int.OfPoint(DB.XYZ(p[0] * M2FT, p[1] * M2FT, p[2] * M2FT))

# --- level & type ---
levels = list(DB.FilteredElementCollector(doc).OfClass(DB.Level))
if LEVEL_NAME:
    levels = [l for l in levels if l.Name == LEVEL_NAME]
if not levels:
    forms.alert("Level not found.", exitscript=True)
level = sorted(levels, key=lambda l: l.ProjectElevation)[0]

ttypes = list(DB.FilteredElementCollector(doc).OfClass(DB.ToposolidType))
if TYPE_NAME:
    ttypes = [t for t in ttypes
              if t.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM).AsString() == TYPE_NAME]
if not ttypes:
    forms.alert("No Toposolid type found in this model.", exitscript=True)
ttype = ttypes[0]

# --- boundary loops (flat sketch at the level) ---
zlev = level.ProjectElevation
MIN_SEG = 0.011  # ft, just above Revit's short-curve tolerance

def make_loop(coords_m):
    pts = [to_internal(p) for p in coords_m]
    flat = [DB.XYZ(p.X, p.Y, zlev) for p in pts]
    loop = DB.CurveLoop()
    n = len(flat)
    for i in range(n):
        a, b = flat[i], flat[(i + 1) % n]
        if a.DistanceTo(b) > MIN_SEG:
            loop.Append(DB.Line.CreateBound(a, b))
    return loop

loops = [make_loop(data["outer_loop"])]
holes_used = 0
if MIN_HOLE_AREA_M2 is not None:
    for h in data["holes"]:
        if h["area_m2"] >= MIN_HOLE_AREA_M2 and len(h["points"]) >= 3:
            loops.append(make_loop(h["points"]))
            holes_used += 1

# --- shape points ---
pts_m = list(data["interior_points"])
if MAX_INTERIOR_POINTS and len(pts_m) > MAX_INTERIOR_POINTS:
    xs = [p[0] for p in pts_m]; ys = [p[1] for p in pts_m]
    area = (max(xs) - min(xs)) * (max(ys) - min(ys))
    cell = math.sqrt(area / float(MAX_INTERIOR_POINTS))
    grid = {}
    for p in pts_m:
        grid.setdefault((int(p[0] / cell), int(p[1] / cell)), p)
    pts_m = list(grid.values())
if INCLUDE_BOUNDARY_POINTS:
    pts_m = data["outer_loop"] + pts_m

points = [to_internal(p) for p in pts_m]

# --- sanity: are we near the internal origin? ---
cx = sum(p.X for p in points) / len(points)
cy = sum(p.Y for p in points) / len(points)
dist_ft = math.sqrt(cx * cx + cy * cy)
if dist_ft > 52800:  # 10 miles
    forms.alert("Transformed geometry lands {:.1f} km from the internal origin - "
                "the model's shared coordinates don't match the DUB41 ITM setup. "
                "Aborting before creating junk.".format(dist_ft * 0.3048 / 1000.0),
                exitscript=True)

# --- create ---
t = DB.Transaction(doc, "Import Composite EG+FG toposolid")
t.Start()
topo = DB.Toposolid.Create(doc, loops, points, ttype.Id, level.Id)
t.Commit()

forms.alert("Toposolid created (id {}).\n{} shape points, {} hole(s), "
            "site centre {:.0f} m from internal origin.\n"
            "Survey position of internal origin: E {:.3f}  N {:.3f}  "
            "elev {:.3f} (ft), angle {:.4f} deg.".format(
                topo.Id, len(points), holes_used, dist_ft * 0.3048,
                pp.EastWest, pp.NorthSouth, pp.Elevation,
                math.degrees(pp.Angle)))

# Revit 2023 or earlier: no Toposolid class. Swap the create block for:
#   topo = DB.Architecture.TopographySurface.Create(doc, points)
# (points only - no boundary loop, no holes; Revit clips nothing.)
