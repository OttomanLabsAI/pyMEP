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
  exports/                    # default output folder, per-Revit-file
  lib/                        # shared IronPython modules used by the buttons
  pyMEP.tab/
    00_Setup.panel/             # 'pyMEP': Settings / Download Latest / Install Update (stacked)
    01_Civil3DConversion.panel/ # Create LandXML Dashboard, Place Boxes/Cylinders/Pipes, Create Pipe Sizes
    02_Modelling.panel/         # Encasement, Gully to MH, Cut Toposolid
    03_Chambers.panel/          # 'Chamber Drawing Setup': sections workflow, Chamber Plans
    04_Parameters.panel/        # Replicate Parameter
    05_Annotate.panel/          # 4 annotation buttons
```

6 panels, 21 buttons, every one with its own icon.

## Panels

### pyMEP (setup)

**Settings** - central configuration for every other button. Two-level menu
writing `%APPDATA%\pyRevit\pyMEP_settings.json`. Also contains
*Open active export folder* (the old standalone Open Folder button was removed;
this menu item is its home now).

**Download Latest** - fetches the newest published pyMEP.extension from
GitHub (latest release, else newest tag, else the default branch) and saves it
to your Downloads folder as `pyMEP.extension.zip`, ready to deploy. Compares
against the installed `version.txt` first. Uses the `github_repo` /
`github_token` / `update_downloads_folder` settings keys.

**Install Update** - deploys `Downloads\pyMEP.extension.zip` over the live
extension the same way the repo's `supersede_pyExtensions.py` does: the
current folder moves to `00 - Superseded\pyMEP\pyMEP.extension_<timestamp>`,
the zip is extracted into place and archived alongside, then pyRevit offers to
reload. Rolls the move back automatically if anything fails; if Windows won't
release the live folder, nothing is touched and it points you at
`supersede_pyExtensions.py` instead.

### Civil 3D Conversion

**Create LandXML Dashboard** - opens the utilities 3D dashboard in the default
browser. It starts EMPTY with a Browse button (or drag & drop) asking for a
Civil 3D LandXML pipe-network export; the file is parsed right in the
browser (same rules as `pymep_landxml.py`: Center = "northing easting",
inverts by flowDir) and the buried-utilities networks are rendered in 3D.
The landing screen also asks how the XML's pipe diameters are stored - mm
(default, the Civil 3D convention) or m - and whether null structures
start hidden (default yes; they load as their own NULL STRUCTURES layer
you can toggle back on in the Networks list). Both exports only include
what is currently turned ON in the dashboard. Two export buttons - **Export structs** and **Export
pipes** - write the JSON files the placement buttons below consume.
Fully offline (three.js is inlined). The dashboard is a self-contained
HTML app bundled in `<extension>/dashboard/`; the button launches the
newest `.html` in that folder, so upgrading the viewer is just dropping the
new file in (`dashboard_html_path` in settings overrides it).

**Place Boxes** / **Place Cylinders** - place every box (rectangular) or
cylindrical chamber from an OttomanLabs utilities-dashboard export: pick the
family, pick the `.json` exported from the 3D viewer's EXPORT button, pick a
workset. One type per layer is duplicated from the picked type; dimensions and
rim/sump/depth go to instance parameters, the structure name to Mark and the
description to Comments.

**Place Pipes** - places Revit pipes from a dashboard PIPES export (the
viewer's Export pipes button): pick layers, map each layer to a workset (remembered between
runs), pipe type / system type / host level from Settings with pickers as
fallback, sizes silently ensured on the configured segment, Marks from the
pipe names, diameters snapped to the pipe type's sizes. Survey transform:
the Settings offsets first, then the model's own survey position; if
neither fits, it offers to place at the internal origin using the
export's own origin (optionally saving it to Settings). Rectangular duct-bank
rows are skipped - only circular runs become pipes.

**Create Pipe Sizes** - reads a dashboard pipes export, lists the
distinct circular diameters and adds the missing ones to the pipe
Segment configured in Settings > Pipes (Place Pipes already does this
automatically; keep for adding sizes without placing pipes).

### Modelling

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

**Gully to MH** - selection-driven: connects gully outlets to a manhole with
downpipe + bend + falling run. Modes are inferred from the selection
(gully+MH, many gullies + one MH, gully only, MH to picked point). Numeric
prompts (downpipe length, invert offset, slope) are remembered between runs.

**Cut Toposolid** - excavates a Toposolid using the bottom outlines of the
selected MEP elements (vertical cut, +50 mm above the top so it always breaks
the surface). Cutter instances stay in the model tagged with the comment
`pyMEP_TopoCut`; deleting a cutter removes its cut.

### Parameters

**Replicate Parameter** - generic utility: pick a placed family type, a
source parameter and a writable target parameter; the value is copied onto
every placed instance of that type, with a preview table and safe type
coercion.

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
`<extension>/exports/<model>/chamber_section_links.json`.

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
| `github_repo` | `owner/repo` the update buttons talk to (default `OttomanLabsAI/pyMEP`; not in the Settings dialog) |
| `github_token` | optional GitHub personal-access token for Download Latest (private repo / rate limits) |
| `update_downloads_folder` | override the Downloads folder used by the update buttons |

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
| `pymep_dashboard.py` | place chambers from a utilities-dashboard JSON export |
| `pymep_dashboard_pipes.py` | read dashboard pipes exports for the LandXML pipe placer |

## Updating

Deployed copies are updated from GitHub, keeping the old version:

1. **Download Latest** (Setup panel) pulls the newest tagged
   `pyMEP.extension` from the repo into `Downloads\pyMEP.extension.zip`.
2. **Install Update** (Setup panel) - or `supersede_pyExtensions.py` from the
   repo root, run outside Revit - moves the live folder to
   `00 - Superseded\pyMEP\pyMEP.extension_<timestamp>` and extracts the zip
   into place.

The deployed version is recorded in `version.txt` (matches the git tag).
