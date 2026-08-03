# pyMEP.extension

A pyRevit extension for MEP BIM workflows: Civil 3D LandXML conversion
(3D dashboard review, chamber + pipe placement), duct-encasement analysis
and rebuild, gully connections, toposolid cutting, annotation and chamber
detailing.

## Install

Clone this folder into your pyRevit extensions directory:

```
%APPDATA%\pyRevit\Extensions\pyMEP.extension\
```

Then reload pyRevit (or restart Revit).

The Encasement workflow additionally needs an external CPython (with numpy /
plotly) for the offline analysis; point `Settings > General > Python executable`
at it. The analysis package ships inside the extension (`conduit_analysis/`).

## Layout

```
pyMEP.extension/
  conduit_analysis/           # standalone CPython analysis (run via external Python)
  dashboard/                  # utilities 3D dashboard (self-contained HTML app)
  exports/                    # legacy per-model output home (live data now in %APPDATA%\pyRevit\pyMEP_exports)
  lib/                        # shared IronPython modules used by the buttons
  pyMEP.tab/
    00_Setup.panel/             # 'pyMEP v<x>': Settings / Install Update (stacked)
    01_Civil3DConversion.panel/ # Civil 3D LandXML Dashboard (split), Place Structures/Pipes, big icon-only: Create Pipe Sizes + Structure to Pipe (stack) and Family at Pipe Top (own slot)
    02_Modelling.panel/         # 'Drainage': Gully to MH, Merge Pipes, Connect Fixtures
    02_Networks.panel/          # 'Networks': Drainage dashboard launch, stacked icons: Apply Edits + Network Settings
    07_PipeNetworks.panel/      # 'Pipe Networks': Lines to Pipes (tracked in-place update) + Inflow Nodes -> Collector Pipes + Sync Input Nodes
    03_Topography.panel/        # Align to Topo, Cut Toposolid, Drape Floor
    04_Chambers.panel/          # 'Chamber Drawing Setup': sections workflow, Chamber Plans
    05_Parameters.panel/        # Replicate Parameter
    06_Annotate.panel/          # 4 annotation buttons
    08_Electrical.panel/        # Encasement (shown before Drainage on the ribbon)
```

9 panels, 31 buttons, every one with its own icon.

## Panels

### pyMEP (setup)

The panel title carries the installed version (e.g. `pyMEP v1.6.0`),
kept in sync with `version.txt` at every release.

**Settings** - central configuration for every other button, in one WPF
window (`SettingsWindow.xaml`): category sidebar on the left (General /
Ducts / Pipes / Annotate / Section Dims / Updates), real controls on the
right, OK / Cancel / Apply at the bottom. Nothing is written to
`%APPDATA%\pyRevit\pyMEP_settings.json` until OK or Apply; blank fields
fall back to the defaults shown in each field's hint. General holds the
folders, Python executable, *Open* for the active export folder and the
output-window auto-close toggle; Section Dims edits the chamber
dimension pairs in a grid; Updates holds the GitHub repo/token and the
install-any-version picker.

**Install Update** - downloads the newest published pyMEP.extension from
GitHub (latest release, else newest tag, else the default branch) and
installs it in one go: the repo zip is repackaged into
`Downloads\pyMEP.extension.zip` (staged write - a failed download never
leaves a truncated zip; if the download fails an existing zip in
Downloads is offered instead), then deployed atomically. The previous
version's folder and the zip are REMOVED after a successful install -
there is no superseded archive; any version stays one click away in
*Settings > Updates*. Anything the old install still holds in its
legacy in-extension `exports/` (tracking registries, project files) is
carried over into `%APPDATA%\pyRevit\pyMEP_exports` first - the
durable per-project home every button reads and writes, so the branch
tracking and stored project files SURVIVE version updates. Every failure
after the swap restores the previous version; if Windows won't release
the live folder, nothing is touched and it points you at
`supersede_pyExtensions.py`. After a successful install pyRevit
RELOADS AUTOMATICALLY - accepting the upgrade is the go-ahead, no
second question (a full Revit restart is still the safest after
ribbon reshuffles). Uses the `github_repo` / `github_token` /
`update_downloads_folder` settings keys.

The Settings window's Updates section holds the **install a specific
version** picker: *Load versions* lists every tagged version from GitHub
(newest first, installed one marked) - pick one and *Install...*
downloads and installs it exactly like Install Update.

**Project Setup** - sets up an MEP model from a JSON config in one run,
four stages in order: worksets, piping system types (donor-duplicated
per classification), view filters, view templates with filter overrides
and workset visibility. Feed it the bundled `configs/default.json`, a
per-project config, or a dashboard MODEL export (one piping system per
layer, worksets from its workset map, one isolation template per
workset plus the full-model *Civil 3D XML Import* template). Run it
twice and everything reports Skipped.

**Project Files** - the file-management window for THIS project: the
files its workflows depend on live together in one managed folder
(`%APPDATA%\pyRevit\pyMEP_exports\<model>\project_files\` with a
small registry - OUTSIDE the extension, so version updates can't touch
it), copied in and
addressed by role. First resident: the project's Civil 3D LandXML - the
Civil 3D LandXML Dashboard opens it by default. Set/replace (file
picker; the original stays put), open the file, open the folder, or
remove; other roles slot in as workflows grow. The stored copy FOLLOWS
its original: where the file was stored from is remembered, and when
that original has changed since (the XML re-exported with new levels),
the dashboard launch re-copies it first - so the dashboard always
shows the file's CURRENT content, no manual re-store. The window
flags such a file as 'Stored (source newer)'. A file stored by an
older pyMEP has no recorded original - re-store it once and it
refreshes itself from then on.

### Civil 3D Conversion

**Civil 3D LandXML Dashboard** (split button) - opens the utilities 3D
dashboard in the default browser. The MAIN click opens it **with this
project's stored data file** (Setup > Project Files): a launch copy of
the viewer is written with the file injected, so the browser goes
straight into the 3D view with no browsing (empty, with a pointer, when
nothing is stored yet). The dropdown's **New Data** opens it EMPTY with
a Browse button (or drag & drop) asking for a fresh
Civil 3D LandXML pipe-network export; the file is parsed right in the
browser (same rules as `pymep_landxml.py`: Center = "northing easting",
inverts by flowDir) and the buried-utilities networks are rendered in 3D.
The landing screen also asks how the XML's pipe diameters are stored - mm
(default, the Civil 3D convention) or m - and whether null structures
start hidden (default yes; they load as their own NULL STRUCTURES layer
you can toggle back on in the Networks list). Hidden nulls start OFF
in the Schedule tab too; turning the layer back on (its eye, All on,
or Isolate Net from a popup) re-ticks their schedule row in the same
click. The footer flags a network named like '... (3)' as a Civil 3D
COPY - if levels were edited on the live network, such an export
carries the copy's OLD levels, which is the usual reason the dashboard
'does not show the new heights'. Chamber solids span cover level down
to the UNDERSIDE of the floor slab when the style desc names one
('... wall 150 floor' = 150 mm below the sump) - matching the DWG's
3D body, which reads floor-thickness deeper than the Sump Level
parameter; the popup shows both ('Sump level' and 'Floor u/s'), and
the model export carries `floor_m` alongside the unchanged hydraulic
`sump_m`. One export button -
**Export model** - writes the `MODEL-*.json` the placement buttons
below consume: everything currently turned ON in the dashboard,
structures AND pipes in one file (turn layers off first to export a
subset). Place Structures and Place Pipes both read it; the older
single-kind `STRUCTS-*` / `PIPES-*` files stay readable too.

**Workset settings** (button on the landing screen and in the sidebar
bar) - list your Revit worksets and give each one the layer names that
belong on it; after a model is loaded the layers appear as clickable
chips. The map is saved in the browser and embedded in every model
export as `workset_map` - the placement buttons read it and pre-fill
their layer -> workset mapping (it wins over the locally saved map;
anything unmapped still gets the usual pickers).
Fully offline (three.js is inlined). The dashboard is a self-contained
HTML app bundled in `<extension>/dashboard/`; the button launches the
newest `.html` in that folder, so upgrading the viewer is just dropping the
new file in (`dashboard_html_path` in settings overrides it).

**Place Structures** - places every box AND cylindrical chamber from a
dashboard export (`MODEL-*.json` or `STRUCTS-*.json`) in one run,
driven from a single setup window: browse to the export, highlight the
layers to place, assign each a workset (pre-filled from the export's
workset map / previous runs), pick the family per shape present -
CATEGORY first (Generic Models, Plumbing Fixtures, ...), then the type
- and optionally give every instance the piping system type named
exactly like its layer (same automation as the pipes; created by
Project Setup). Then map each family's L/W/H/DIA
instance parameters. Each family's vertical origin is auto-detected
(base / top / mid-height, probe instance in a rolled-back transaction)
so the chamber lands with its sump, rim or centre at the right level.
One type per layer is duplicated from each picked type; dimensions and
rim/sump/depth go to instance parameters, the structure name to Mark,
the description to Comments, the derived rotation to the instance.

**Place Pipes** - places Revit pipes from a dashboard export
(`MODEL-*.json` or `PIPES-*.json`): pick layers, map each layer to a workset (remembered between
runs), pipe type / system type / host level from Settings with pickers as
fallback, then pick the pipe Segment for the placed pipes (configured
one offered first; or leave it to the type's routing preferences) - the
export's sizes are ensured on that segment, it is written to every
pipe's 'Pipe Segment' instance parameter, and diameters snap to its
size list. Marks come from the pipe names. Survey transform:
the Settings offsets first, then the model's own survey position; if
neither fits, it offers to place at the internal origin using the
export's own origin (optionally saving it to Settings). Rectangular duct-bank
rows are skipped - only circular runs become pipes.

Both placement windows ask for the VERTICAL DATUM - the level the
export's site levels are measured ABOVE: a 47.85 m invert lands
47.85 m over the picked level, so the placed elements' displayed
elevations read the site values (the same convention as the Lines to
Pipes work-plane datum). A level named like 'Datum' is preselected
when the model has one; the pick is remembered and SHARED between
Place Pipes and Place Structures so chambers and pipes always land on
one vertical. Choose the '(numeric Z offset from Settings)' option to
keep the old absolute-Z behaviour.

**Create Pipe Sizes** (stacked, big icon-only - the name lives in the tooltip) - reads a dashboard pipes
export, lists the distinct circular diameters and adds the missing ones
to the pipe Segment configured in Settings > Pipes (Place Pipes already
does this automatically; keep for adding sizes without placing pipes).

**Structure to Pipe** (small, stacked under Create Pipe Sizes) -
selection-driven one-off: replaces a cylinder structure (a Generic
Cylinder Plumbing Fixture carrying `DIA` + `H`, the placeholder used
for vertical risers) with a real vertical Revit pipe of the same
diameter and length. Each pipe stands on the cylinder's base at its
EXACT XY (base Z from the instance bounding box, so the family's
vertical origin doesn't matter), runs one `H` up, and takes the
cylinder's System Type, Mark and Comments; the pipe type is the
Settings default (else the first in the model). The original cylinders
are deleted. Select one or more and confirm - the whole selection is
converted in ONE transaction (one undo step) and Revit's warning
dialogs are dismissed as they arrive, so a big batch never stops to
ask. A cylinder that cannot be read is reported and skipped; the rest
still convert.

**Family at Pipe Top** (its own column, big icon with no label - the
name lives in the tooltip) - the inverse of Structure to Pipe: select what the family
should sit on, pick a family type (category > family > type, with a
search box that matches all three at once) and one instance is placed
on the top of each. Anything drawn as a line - pipe, conduit, duct,
cable tray - gives the higher of its two ends (the head of a riser, the
upstream end of a graded run; ties go to the start end); a placed
family gives its own XY at the top of its bounding box. The instance
lands on the host's own reference level and is then nudged vertically
onto the point, so the family's vertical origin does not matter. Tick
*Delete the original once the family is placed* to have each host
removed after its family lands (a host whose family did not place is
always kept). All in one transaction - one undo step - and the choice
of family + checkbox is remembered between runs.

### Pipe Networks

**Lines to Pipes** - draw the layout in plan as model lines, then turn
the lot into a graded pipe network in one run. Filter the lines by line
style and workset - the default filter '(styles starting
with the prefix)' takes every line whose style name starts with the
Style prefix box ('Pipes' by default), so one run covers the whole network; a zero count says WHY (wrong
workset vs. style not in use); pick the system type, the PIPE SEGMENT (the
material/schedule choice from the pipe properties - e.g. 'Plastic -
Schedule 40') with the diameter list showing that segment's catalogued
sizes, optionally a pipe type ('(automatic)' uses the first), a DEFAULT
gradient 1:n and the invert level at the outfall; then click a line near its
outfall end. Every line becomes a pipe falling toward that point AT
ITS OWN gradient, read from its line style name: 'Pipe 1-80' runs at
1:80, 'Pipe 1-150' at 1:150 ('1:80'-style names work too). A style
with no number uses the dialog's default gradient. Lines on a 'Slope
Custom' style open a clickable plan of the whole network first: custom
lines show orange, click one, type its 1:n, Apply - it turns green and
the next pending one is selected; OK needs every custom line answered (invert convention as everywhere in pyMEP: centreline =
invert + dia/2). Where a line crosses or ends on another, the branch
is teed into the through run at that run's level; where two lines meet
end to end they are elbowed. The solver is built for real drawings: a
lateral drawn crossing its main with up to ~2 m of overshoot is
trimmed at the junction; the same run drawn twice keeps only the
longer copy; a line that touches nothing on the way to the outfall is
reported and skipped, never guessed at. Everything happens in one
transaction with Revit's warning dialogs dismissed as they come.
Placed 'Node - Invert Level' marker families (any family whose name
contains 'invert') replace the outfall pick entirely: each node pins
the level typed into its 'Invert Level' parameter (measured above the
LINES' work plane) at its spot as the network's HIGH point - the
node is the inlet, and the pipes FALL AWAY from it along the node's
direction at their line gradients. Several nodes split the network
between them; where two could feed the same run the higher feed wins
and the clash is reported, not fudged.
Every build is TRACKED (project_files/lines_network.json), and
re-running is an IN-PLACE update: each existing pipe is re-graded by
re-setting its location curve, so the element ids survive and TAGS IN
DRAWINGS KEEP THEIR HOSTS. Only the fittings are deleted and rebuilt
(a connected tee blocks a curve change); pipes whose line is gone or
now splits differently are swept afterwards; a pipe that will not
take its new geometry is replaced with a fresh one and logged. A
record written by an older pyMEP has no per-line pipe map, so the
first re-run does one last delete-and-rebuild - ids are stable from
that run on. Custom-line gradients are remembered per line between
runs.

**Inflow Nodes -> Collector Pipes** - connects node families into the
whole line-built network: select the node
families (or pick them), give the branch gradient, and every node
casts a ray along its FACING direction (family rotation) - the first
network pipe that ray meets is the one it joins, and the branch is
built into exactly that pipe with the full node engine: drop-first or
grade-first per the family's 'Drop Pipe' parameter, oblique approaches
squared for the tee, branch pipe type and system inherited from the
pipe it joins, size from the outlet connector / DIA parameter. A node
whose rotation meets nothing uses the plan-nearest pipe (logged);
already-connected nodes are skipped. Re-running Lines to Pipes rebuilds the MAINS
only - run this button again afterwards to reconnect the nodes.

**Sync Input Nodes** - adapts the tracked branches to the nodes as they
are NOW: untouched nodes are left alone; nodes that moved, TURNED
(rotation re-aims the branch) or had their family's Drop Pipe yes/no
toggled (drop-first vs grade-first) get their old branch deleted, the
main healed across the old tee (the two open halves stretched back
into one pipe), and the branch rebuilt with the same settings against
the main as it now lies; deleted nodes get their branch removed. One
undo step.

### Drainage

**Gully to MH** - selection-driven: connects gully outlets to a manhole with
downpipe + bend + falling run. Modes are inferred from the selection
(gully+MH, many gullies + one MH, gully only, MH to picked point). Numeric
prompts (downpipe length, invert offset, slope) are remembered between runs.

**Merge Pipes** - selection-driven: collapses straight runs of pipe into
single pipes. Select the pipes that make up a run (the couplings between
them can be left unselected) and click - or click with NOTHING selected
and pick the pipes in the view, finishing on the options bar under the
ribbon; every set of pipes that line up
end-to-end - the turn between them can be any angle, offset to the side
up to one pipe diameter, any gaps along the line allowed - is replaced
by ONE pipe spanning the run's
two extreme endpoints at their EXACT XYZ (nothing re-projected or
rounded). The originals and the couplings that sat entirely inside the
run are deleted; fittings where the run meets the rest of the model
(elbows, tees) are kept and reconnected to the new pipe's matching end.
The new pipe inherits the run's longest segment - pipe type, system
type, level, Mark, comments - and the run's diameter (the largest when
a run mixes sizes, reported). The workset is kept when every merged
pipe shares one; when they differ the new pipe lands on the ACTIVE
workset and the report says so. A settings dialog opens first: re-grade
each merged pipe at `1 : n` (remembered), choosing whether the TOP or
BOTTOM end's level stays exactly as it is (the other end's Z derives
over the plan run; XY never moves) - or untick to keep both extreme
endpoints exactly. Runs with a gap larger than a
coupling are flagged in the confirm dialog; selected pipes that line up
with nothing are left untouched.

**Connect Fixtures** - selection-driven: pick ONE pipe (the main run)
and any number of plumbing fixtures; each fixture gets a vertical
downpipe from its outlet connector, an elbow, a sloped branch falling
at 1:n toward the main, and a TEE JUNCTION where it meets it - the
main is split at the branch point and the two halves + branch joined
with a tee (a takeoff only as a reported fallback). Successive tees
keep splitting the main; each fixture ties into whichever piece spans
its position. One proper dialog drives it: branch diameter in mm
(default: the fixture outlet size, snapped to the main type's routing
sizes), the branch slope ratio `1 : n`, optionally re-grading the MAIN
at its own `1 : n` before anything is drawn (its low end stays put,
the field is prefilled with the main's current gradient), and the
upstream invert - by default it stays where the model currently puts
it (each branch meets the main's centreline as it lies and the elbow
level derives back up the slope; the dialog shows the first fixture's
resulting invert), or untick and type an absolute level to fix the
elbow invert for every fixture instead. Branches take the main's pipe
type, system type and level; diameter and slope are remembered between
runs. A fitting that can't be placed never fails the branch - the
pipes stay and the miss is reported.

### Networks

Networks are COLLECTOR runs. Node type names carry just system and
flow (`STORMWATER - IN` - no network numbers to keep unique), and
every collector pipe a series of branches feeds into gets its own
automatic identity: `STORMWATER - IN - C1`, `C2`, ... (reused from
what's already stamped or tracked, so numbers stay stable). Everything
a collector owns - nodes, branch pipes, fittings (including the
couplings Revit inserts by itself) and the collector's pieces -
carries that name in a **`pyMEP_Network`** instance parameter (a
shared parameter pyMEP binds automatically, under Identity Data). Nodes to
Main, Sync Input Nodes and Apply Edits stamp it on everything they build;
the dashboard launch backfills older models from the tracking
registry. The parameter IS the network map: schedule it, filter views
by it, and ADD any manually drawn pipe to a network by just typing the
value in its Properties - it shows up in the dashboard as a stamped
extra. A value with no nodes at all becomes its own dashboard network.

**Drainage** (the big launch button) - opens the drainage networks 3D dashboard. It scans
every placed family whose FAMILY name contains the Network Settings
filter word (default `node`), groups the instances into networks by
their TYPE name (`STORMWATER - IN - N1` reads as system STORMWATER,
flow IN, network N1), joins them with the tracked branches and the
mains they tee into, and opens the lot in a browser 3D viewer (same
design as the LandXML dashboard, fully offline). The dashboard is
REBUILT from the model + registry on every launch, so running Nodes to
Main populates it automatically. Click a network in the viewer to edit
it: branch diameter / gradient / pipe + system type, main diameter /
gradient (upper or lower end kept), the INVERT LEVEL of the main's
upper or lower end, and the workset - Preview redraws the network in
3D, and **Undo** steps the dashboard's pending edits back one change
at a time (field edits, previews, reverts - the 3D view, the edit card
and the documented file all follow). **Save changes for Revit**
documents everything into ONE
`pymep_network_edits.json`: the first save asks where the file lives
(Chrome/Edge - the browser then keeps writing to that same file on
every further change, automatically), so the model update is a single
click on Apply Edits. Browsers without that support download a fresh
copy per save instead. Every save carries a timestamp.

**Apply Edits** (stacked, big icon) - picks the newest edits file out of
the configured folder (default: Downloads) and adapts the model to it:
mains resized, re-graded and set to the typed end invert, worksets
moved, and every tracked branch teeing into a touched main
delete-healed-rebuilt against the main as it now lies (the same
machinery as Sync Input Nodes) - one undo step. The applied save's
timestamp is remembered, so the same save never applies twice - while
the edits file itself stays in place for the dashboard to keep
writing to. Asks first unless the confirm toggle is off.

**Network Settings** (stacked, big icon) - the dialog behind both buttons:
the INPUT-NODE filter word (families whose family name contains it),
the folder the dashboard's saved edits land in (blank = Downloads), and
whether Apply Edits asks before changing the model. Saved per user.


### Parameters

**Replicate Parameter** - generic utility: pick a placed family type, a
source parameter and a writable target parameter; the value is copied onto
every placed instance of that type, with a preview table and safe type
coercion.

### Topography

**Align to Topo** - drops family instances onto a surface: pick the
family types (searchable checkbox list of every placed Family : Type),
pick Toposolids / Topography / Floors (a pre-selection is used when you
have one), and every instance of the chosen types gets its Elevation
from Level set so it sits on the TOP of the chosen surfaces at its own
X,Y (vertical projection, nearest hit - so stacked surfaces resolve to
the top one). Instances not above any chosen surface, or already on it
(within 0.5 mm), are reported and left untouched.

**Cut Toposolid** - excavates a Toposolid using the bottom outlines of the
selected MEP elements (vertical cut, +50 mm above the top so it always breaks
the surface). Cutter instances stay in the model tagged with the comment
`pyMEP_TopoCut`; deleting a cutter removes its cut.

**Drape Floor to Topo** - pick a floor, then a toposolid / topography /
floor to follow: every slab-shape sub-element point of the floor is
moved to the surface's level at that X,Y (vertical projection, nearest
hit from above). Prior shape edits are reset first so the result
follows the surface exactly at the floor's points; add points to the
floor (Shape Editing > Add Point) and re-run for a denser drape.
Misses (no surface at that plan position) are reported and stay on the
flat plane.

### Chamber Drawing Setup

**Chamber Sections** (four buttons) - the chamber detailing workflow in ribbon
order:

* *Create Sections* - creates four named section views (`{Mark} SIDE A..D`)
  around each chosen chamber, aligned to its rotation, and **auto-writes the
  chamber-section association records** - so the normal workflow is just
  Create Sections, then Update Positions later. No separate Associate run
  needed for sections made here.
* *Update Positions* - re-finds each associated chamber (Mark first,
  ElementId fallback) and moves/rotates its section views back into position
  after chambers have moved. Preview + one confirm. This is the recurring
  button.
* *Match Sections* - for manually-drawn sections only: matches sections to
  their nearest chamber (one dialog pass), then renames them
  (`{Mark} SIDE A/B`) and/or stores associations - the old Rename and
  Associate buttons merged into one matching run.
* *Dimension Section* - with a chamber section view active, one click creates
  the column/row chained dimensions, chamber width/height dimensions (between
  the reference-plane pairs configured in Settings), and a spot elevation on
  every duct centreline.

**Chamber Plans** - creates a scope box per chamber (copied from a seed box,
preferring one named `sample_scope_box`), then creates a cropped plan view for
every chamber scope box that doesn't have one yet - including boxes from
earlier runs. Idempotent: existing boxes/views are skipped, and a preview
confirm lists what will be created.

Associations are stored per model in
`pyMEP_exports/<model>/chamber_section_links.json` (in %APPDATA%\pyRevit).

### Annotate

**Annotate** (four buttons) - pipe-annotation tools, all working on a
pre-selection in the active plan view:

* *Annotate Ducts* - one two-line TextNote for a bank of parallel
  pipes/conduits (`3x2 + 2x1 - 8No.110Ø` style, grid decomposition with one
  leader per sub-rectangle), suffix line from Settings.
* *Annotate Pipes* - one `160mm @ 1:200` TextNote per selected pipe, placed
  perpendicular to the run at the configured offset, leader back to the
  midpoint. No clicks needed.
* *Pipe End Elev* - invert-level spot elevations at both ends of every
  selected pipe.
* *Pipe Dia+Slope* - writes `160mm @ 1:100` into the project parameter
  `MEP_pipe_dia_slope_label` on the selected pipes (or all pipes), for
  tag/schedule-driven labelling. Requires that instance text parameter on the
  Pipes category.

### Electrical

**Encasement** - the old Initialize / Build Ducts / Build Connections trio in
one button:

1. *With a selection* (pipes/conduits + fittings): prompts for concrete cover,
   exports the pipework CSVs, runs the offline analysis (`conduit_analysis/`)
   through the configured external Python, and opens the 3D / plan HTML views
   for review. Then one confirm - "Build ducts + connections now?" - places the
   rectangular ducts from the fresh `duct_centrelines_<TS>.csv` and inserts the
   elbow fittings from `plan_bend_outlines_<TS>.csv` (exact same-run timestamp,
   not just "newest file").
2. *With nothing selected*: offers to rebuild ducts + connections from the
   latest analysis CSVs (post-review / repair path).

If the model already contains ducts or elbows with `C#-O#` style Marks from a
previous run, the button warns and offers to delete them first, so re-runs no
longer cross-connect old and new geometry. The report window stays open
whenever anything failed.

Duct type and MEP system type come from `Settings > Ducts`.

## Settings keys

Written by the Settings dialog to `%APPDATA%\pyRevit\pyMEP_settings.json`:

| key | purpose |
| --- | --- |
| `script_folder` | override path to `conduit_analysis/` |
| `python_exe` | external Python executable for the offline analysis |
| `export_folder_override` | override the default per-file export folder |
| `duct_type_name` / `duct_system_type_name` | rectangular duct type + MEP system type used by Encasement |
| `pipe_type_name` / `pipe_system_type_name` | default pipe type + piping system type (Place Pipes) |
| `pipe_host_level` | Revit Level to host placed pipes on |
| `landxml_off_e_m` / `landxml_off_n_m` / `landxml_off_z_m` / `landxml_rot_deg` | survey transform for the placement buttons |
| `landxml_segment_name` | pipe Segment that receives LandXML pipe sizes |
| `dashboard_layer_workset_map` | saved layer-to-workset assignments (Dashboard > Place Pipes) |
| `dashboard_html_path` | override the dashboard HTML that Open Dashboard launches |
| `annotate_suffix` | line 2 of the Annotate Ducts label |
| `annotate_pipe_offset_mm` | perpendicular offset for pipe labels / spot elevations |
| `chamber_dim_pairs` | reference-plane name pairs dimensioned by Dimension Section |
| `gully_downpipe_length_mm` / `gully_invert_offset_mm` / `gully_slope_ratio` | remembered by the Gully to MH prompts (not in the Settings dialog) |
| `networks_filter` / `networks_edits_folder` / `networks_confirm_apply` | Networks dashboard: input-node filter word, edits-file folder (blank = Downloads), ask-before-apply |
| `github_repo` | `owner/repo` the update buttons talk to (default `OttomanLabsAI/pyMEP`; Settings > Updates) |
| `github_token` | optional GitHub personal-access token (private repo / rate limits; Settings > Updates) |
| `update_downloads_folder` | override the Downloads folder used by Install Update |
| `auto_close_output` | close each command's output window when it finishes (error reports stay open) |
| `hide_output` | never show the output window: it is hidden while the command runs and closed at the end (an error or traceback pops it open) |
| `topfam_label` / `topfam_delete` | Family at Pipe Top: last family type used, and whether the original is deleted afterwards |
| `lines_*` | Lines to Pipes dialog memory (style, workset, segment, dia, default gradient, invert, types) |

## Ribbon order on reload

The same startup pass turns the icon-only buttons (the two-high
stacks AND the standalone Family at Pipe Top) into big unlabelled
icons, and leaves a column of three or more alone.

Revit builds each panel's layout ONCE per session, so a button added
to an existing stacked column has nowhere to go until Revit itself is
restarted - a pyRevit reload is not enough, and the new tool simply
looks missing. Install Update says so in its report.

Keep pyMEP stacks to TWO buttons. A three-button stack was tried
repeatedly (with and without resizing, with and without labels, after
full restarts) and the third row never rendered - the extra tool goes
in its own panel slot instead.

Revit's ribbon API cannot MOVE a panel in a running session, so a
pyRevit reload appends any renamed panel at the end of the tab - and
the Setup panel is renamed on every release (its title carries the
version). `startup.py` fixes this: on the first idle moment after
every load/reload it re-sorts the pyMEP tab's panels back into the
layout order through the Autodesk.Windows ribbon, so updates no longer
scramble the tab. (Buttons INSIDE a panel that were renamed by an
update still sit at the panel's end until the next full Revit
restart - that one is not fixable in-session.)

## Lib modules

| module | purpose |
| --- | --- |
| `pymep_config.py` | settings, paths, defaults |
| `pymep_csv.py` | CSV read/write helpers |
| `pymep_revit.py` | unit conversions, element/connector helpers |
| `pymep_log.py` | tee logging to pyRevit output + log file |
| `pymep_export.py` | export pipework data from the active document |
| `pymep_build.py` | build ducts from a centrelines CSV |
| `pymep_connect.py` | build elbow connections between ducts |
| `pymep_landxml_place2.py` | pipe placement engine (survey transform) |
| `pymep_structures_place.py` | structure placement helpers |
| `pymep_pipesizes.py` | add pipe sizes to a segment |
| `pymep_gully_connect.py` | gully-to-manhole pipe modelling |
| `pymep_chamber_links.py` | chamber-section association records |
| `pymep_topo_cut.py` | cut a Toposolid with MEP bottom outlines |
| `pymep_topo_align.py` | align family instances to a surface top |
| `pymep_dashboard.py` | place chambers from a utilities-dashboard JSON export |
| `pymep_dashboard_pipes.py` | read dashboard pipes exports for the LandXML pipe placer |

## Updating

Deployed copies are updated from GitHub:

**Install Update** (pyMEP panel) downloads the newest tagged
`pyMEP.extension` from the repo and deploys it atomically (the previous
folder is removed after success - reinstall any version from
*Settings > General > Downgrade / reinstall*). Outside Revit,
`supersede_pyExtensions.py` (repo root) deploys a downloaded
`Downloads\pyMEP.extension.zip`, keeping a superseded copy.

The deployed version is recorded in `version.txt` (matches the git tag).
