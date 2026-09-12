# pyMEP versus the Nexar Group suite

**pyMEP at v1.260.0 (12 September 2026) against Nexar Docs 1.0.9, Nexar Plan
and the unreleased Nexar Core.**

## Read this first

Everything said here about Nexar comes from vendor marketing copy (the Autodesk
Design and Make Marketplace listing and nexar-group.com). None of it has been
independently verified, there are no third-party reviews, and nothing below was
tested against a trial. Every Nexar capability in this document is **asserted,
untested**. Where a row's verdict depends on a Nexar claim actually being true,
the row says so.

Everything said here about pyMEP comes from reading the repository, not from
its README or from what its author says it does: all 46 pushbutton scripts, all
47 `lib/pymep_*.py` modules, `startup.py`, the 26 test files, the CI workflow
and the installer. Counts and line numbers are as of v1.260.0; the sharpest
claims (the association-loss path, the unwrapped text-note loop, the
spot-elevation regex, the warning suppressor's call sites, the release cadence)
were re-checked by hand after the read.

## The verdict in five lines

1. **The two products barely overlap.** Nexar Docs is a generic building
   documentation suite (walls, doors, rooms, schedules, COBie). pyMEP is a
   civil and external-services modelling toolkit (gravity drainage, chambers,
   conduit banks, fencing, kerbs, terrain) with a chamber drawing pipeline
   bolted on. Of the 46 buttons, about eight touch anything Nexar sells.
2. **Where they do not overlap, pyMEP is far ahead** and Nexar has nothing:
   drainage grading with heads and merges, chamber vertical-origin resolution,
   encasement sizing, terrain-draped setting-out, Civil 3D interchange, the
   view-template transfer.
3. **Where they do overlap, pyMEP is behind on engineering, not on ideas.** Its
   annotation and dimensioning code has no warning suppression, rolls back
   whole batches on one failure, ignores linked models, and has no collision
   avoidance. Those are exactly the four things Nexar's reliability copy leads
   with. If Nexar's claims hold, they are better at this than pyMEP is.
4. **Seven Nexar modules have no answer in pyMEP at all**: tagging, schedules,
   Excel sync, COBie and classifications, data-standards rules, model health
   and cleanup, clash detection, scheduled runs. Not partial. Absent.
5. **Worth buying:** a 14-day trial of Nexar Docs against three specific tests
   (below), as a supplement. Nexar Plan is not worth buying for this work.
   Nothing Nexar sells replaces any part of pyMEP.

---

## 1. Feature matrix

Legend for the last column: **Replace** means buying would make a pyMEP
feature redundant. **Supplement** means it fills a hole pyMEP does not fill.
**Nothing** means buying changes nothing for this row.

### Nexar Docs, module 1: automated dimensioning

| Nexar claim (asserted) | pyMEP does this? | Where | How completely | Buying would |
|---|---|---|---|---|
| One-click dimensioning in floor plans | No | Nothing dimensions a plan. `NewDimension` occurs in one file, `04_Chambers.panel/06_DimensionSection.pushbutton/script.py`, and it refuses anything that is not `ViewType.Section` (`:903`, `:907-912`). | 0% | Supplement |
| Dimensioning in sections | Partial | `06_DimensionSection`: reference planes named `x1..`, `y1..`, `z1..` on the largest family instance in view, plus pipe, conduit and duct centreline spacing, driven by a rule list (`lib/pymep_dim_rules_ui.py`, `lib/pymep_chamber_sections.py:418-628`). | Chamber sections only. Nothing else in a section is dimensioned. | Supplement, not replace: Nexar will not know your `z1..z6` planes. |
| Elevations and 3D views | No | | 0% | Supplement |
| Walls, openings, grids, curtain walls, columns, casework, slab edges, overall widths | No | No code references walls, grids or curtain walls for dimensioning. | 0% | Supplement, if your sheets need it. Chamber sheets mostly do not. |
| Linked-model walls and openings with transforms handled | No | No `Reference.CreateLinkReference` anywhere in the repo. `06_DimensionSection` collects host-only (`:212`, `:540`). | 0% | Supplement |

### Nexar Docs, module 2: automated tagging

| Nexar claim (asserted) | pyMEP does this? | Where | How completely | Buying would |
|---|---|---|---|---|
| Automatic tag placement | No | `IndependentTag` appears nowhere. The only "Tag" hits are WPF `CheckBox.Tag`. `06_Annotate.panel/00_Annotate` places **text notes** (`TextNote.Create` at `:570`), not tags; `02_PipeEndElev` places spot elevations; `03_PipeDiaSlopeLabel` writes a string into a project parameter for someone else's tag family to show. | 0% for tags. The text-note labeller covers pipe, conduit and duct banks in plan only (`:112-121`). | Supplement |
| Avoids crowded regions, leader overlaps, occlusion | No | Nothing reads existing annotation. Annotate pushes every label one fixed vector off the bank (`00_Annotate:498-504`, `:552-559`). Dimension Section's only spacing logic is a per-run slot counter reset every run (`06_DimensionSection:506`). | 0% | Supplement |
| Doors, windows, rooms | No | No `OST_Rooms`, doors or windows anywhere. | 0% | Supplement |
| Structural and MEP components | No | | 0% | Supplement, and this is the one that matters for your sheets. |
| Linked-tag support | No | | 0% | Supplement |

### Nexar Docs, module 3: schedules and sheets

| Nexar claim (asserted) | pyMEP does this? | Where | How completely | Buying would |
|---|---|---|---|---|
| Schedule creation with rules, formulas, multi-category | No | `grep -ri viewschedule` over the repo returns nothing. | 0% | Supplement |
| Two-way Excel sync | No | No Excel, xlsx or openpyxl anywhere. The only CSV is one-way pipe geometry for the offline encasement analysis (`lib/pymep_csv.py`, `lib/pymep_export.py`). | 0% | Supplement |
| Automated sheet generation | Partial | `04_Chambers.panel/00_SheetsPipeline` creates numbered sheets from a `{n}` pattern with one title block and N chambers per sheet (`:937-961`, `lib/pymep_chamber_sections.py:162-171`); `03_SheetSetup` lays viewports out (`lib/pymep_sheet_setup.py:233-266`). | Chamber drawings only. No sheet parameters, no revisions, no sheet sets from a list. The layout has no right or bottom margin, so views land on a right-hand title strip, and overflow is counted, not fixed (`pymep_sheet_setup.py:254-259`, `03_SheetSetup:564-567`). | Nothing for chambers (Nexar will not build a chamber sheet). Supplement for anything generic. |
| Room data sheets | No | | 0% | Supplement, if a client asks. Unlikely for external services. |

### Nexar Docs, module 4: COBie and data standards

| Nexar claim (asserted) | pyMEP does this? | Where | How completely | Buying would |
|---|---|---|---|---|
| Region-aware COBie export, OmniClass and Uniclass picklists | No | Zero hits for COBie, OmniClass, Uniclass, Uniformat, MasterFormat. | 0% | Supplement. UK data-centre clients do ask for Uniclass and COBie; this is a real hole. |
| Saveable category-mapping presets | No | The nearest thing is the new named dimension sets (`lib/pymep_chamber_sections.py`, `dim_sets`) and fence configs (`03_Fencing.panel/00_FenceConfigs`). | Pattern exists in two places, not for mapping. | Supplement |
| Validation pipeline | No | | 0% | Supplement |
| Data-standards rules on selected categories, "test on sample" dry run, one-click auto-fix, one undoable transaction | No | No rule engine, no auto-fix. The only dry run in the codebase is the import pre-flight in `lib/pymep_vt_deserialize.py:924-1133`; the only one-undo batches are `TransactionGroup` uses in seven places. | Adjacent patterns only. | Supplement |

### Nexar Docs, module 5: classifications

| Nexar claim (asserted) | pyMEP does this? | Where | How completely | Buying would |
|---|---|---|---|---|
| Batch OmniClass, Uniformat, MasterFormat, Uniclass assignment with a compiled-regex rule engine | No | The closest is `00_Setup.panel/ProjectSetup.pushbutton/setup_lib.py:631-658`: a hard-coded substring table that guesses a Revit piping system classification from a CAD layer name and defaults everything unknown to `Sanitary`. Not regex, not user-editable, not a classification system. | 0% | Supplement |

### Nexar Docs, module 6: model health and cleanup

| Nexar claim (asserted) | pyMEP does this? | Where | How completely | Buying would |
|---|---|---|---|---|
| Dashboard: file size, warnings, in-place families, modelling practice | No | `doc.GetWarnings()` is never called. Nothing tests `Family.IsInPlace` except the new export button's skip list. | 0% | Supplement |
| Cleanup module with presets and faster purging | No | Every "purge" hit is the `del sys.modules` idiom at the top of a button. | 0% | Supplement |
| Scheduled headless runs, run history | No | No scheduler, no CLI entry point, no run history. "Headless" in pyMEP means one button driving four others in-session through a dict on `sys` (`00_SheetsPipeline:833-858`). | 0% | Supplement |

### Nexar Docs, module 7: clash detection

| Nexar claim (asserted) | pyMEP does this? | Where | How completely | Buying would |
|---|---|---|---|---|
| Lightweight internal clash checks, Excel switchback, 2D and 3D views, PDF and HTML reports | No | Zero hits for interference or collision. Every "clash" hit is a name-collision comment or the import dialog's same-name radio. `lib/pymep_section_cut.py:71-101` tests pipe-versus-plane to choose which sections to keep and throws the result away. | 0% | Supplement, but note the listing describes no resolution guidance either. |

### Nexar Docs, module 8: reliability

| Nexar claim (asserted) | pyMEP does this? | Where | How completely | Buying would |
|---|---|---|---|---|
| Batch failure handling: one bad reference does not kill a run of 800 | Partly, inconsistently | Good: `06_DimensionSection` returns a text row per failed string and carries on; `00_SheetsPipeline` uses a `TransactionGroup` with a cancellable progress bar; `ImportProjectData` and `ProjectSetup` isolate per stage. Bad: `00_Annotate:565-597` rolls back every label if one `TextNote.Create` throws; `01_ChamberPlans:1571-1735`, `02_CreateSections:1118-1169`, `03_SheetSetup:422-534` are all-or-nothing. Across the repo, 49 `try / Commit / except RollBack` blocks in 33 files discard the whole batch. | Roughly a quarter of loops carry any per-item protection (359 of 1,421). | Supplement for the generic documentation work; nothing for pyMEP's own tools, which need fixing regardless. |
| Warning preprocessors suppressing Revit dialogs | Barely | One `IFailuresPreprocessor` exists, `lib/pymep_replace_structure.py:168-190`, applied at four call sites (`pymep_replace_structure.py:257, 289`, `lib/pymep_lines_to_pipes.py:1025`, `07_PipeNetworks.panel/02_UpdatePipes:505`). The other 70 of 74 transactions have none. No annotation or dimension code has it. | About 5% | Same |

### Cross-cutting claims

| Nexar claim (asserted) | pyMEP | Buying would |
|---|---|---|
| Works on ACC cloud models | No evidence of cloud awareness anywhere. `ModelPathUtils`, `IsModelInCloud`, `GetCloudModelPath`: zero hits. Per-project data folders are keyed on `doc.Title` (`lib/pymep_config.py:129-159`, `lib/pymep_chamber_links.py:42-53`), so two cloud projects with the same model name share a folder. | Supplement for Nexar's own features; does not fix pyMEP's. |
| Host and linked models | Three features read links, each with its own copy of the same idiom: `02_CreateSections:938-957` (section cut test), `03_Topography.panel/02_DrapeFloor:439-640` and `lib/pymep_fence_revit.py:363-650` (terrain). The other 43 buttons are host-only and silently produce partial results in a federated model. | Supplement |
| Revit 2025, 2026, 2027 | pyMEP states no supported version range anywhere. Six hand-rolled shims exist; the one proper compat module, `lib/pymep_vt_compat.py`, is imported by two files. About 100 raw `ElementId.IntegerValue` reads across roughly 30 files (deprecated since 2024). `Toposolid` in 61 places, one guarded. `UnitTypeId` imported at module top level in `lib/pymep_fence_revit.py:29`. Nothing is 2027-aware. | Nothing. This is your burden either way. |
| Logs at `%AppData%\NexarDocs\logs` | `lib/pymep_log.py` tees to `%APPDATA%\pyRevit\Logs\<name>_<timestamp>.log`, one file per run, never pruned, non-ASCII replaced with `?` (`:71`). Used by three commands. | Nothing |
| Support SLA within one business day | One maintainer, no issue tracker in-tree, no crash reporting, no adoption telemetry. Fifty of fifty visible commits by one author. | Supplement, for Nexar's features only. |
| Pricing from £30/user/month, 14-day trial | Not applicable | |

### Nexar Plan: drawing-to-Revit conversion

| Nexar claim (asserted) | pyMEP does this? | Where | How completely | Buying would |
|---|---|---|---|---|
| DWG to native walls, floors, roofs, ceilings, grids, columns, framing, foundations | No | Nothing reads DWG, DXF, PDF or images. `ImportInstance`, `CADLinkType`, `DWGImportOptions`: zero hits. | 0% | Nothing. You do not model buildings. |
| DWG to MEP elements | No, and a different problem is solved instead | pyMEP converts LandXML and dashboard JSON (`01_Civil3DConversion.panel`), and Revit model lines (`07_PipeNetworks.panel/01_LinesToPipes`), into graded pipe networks with inverts, fittings and tees. A DWG line has no invert; Nexar Plan, if it does what it says, would give you ungraded pipes that pyMEP would then have to fix. | Different input, deeper output. | Nothing, unless a trial shows it reads inverts from DWG attributes. Unlikely from the copy. |
| PDF and image support, "being expanded" | No | | 0% | Nothing |
| Works best with clean CAD standards and reviewed mappings | pyMEP has the same dependency, on Revit-side conventions instead: line style names carry gradients (`lib/pymep_lines_to_pipes.py:83-102`), family names must contain `invert` or `node`, type names are `SYSTEM - FLOW`. There is no reviewable mapping document; mapping is a per-run dialog plus hard-coded name lists (`lib/pymep_dashboard.py:104-112`). | | |

### Nexar Core

Not released. Nothing to compare. Do not factor it into a purchase.

---

## 2. What pyMEP has that Nexar has no equivalent for

Every item here is domain logic a generic documentation or conversion product
does not carry and will not build for one customer. Reasons are given per item.

**Gravity drainage grading from drawn lines.**
`lib/pymep_lines_to_pipes.py:431-500` is a hydraulic solver, not a converter:
it orients the network as a tree from the outfall, takes the lower feed at a
merge (`feed[n] = min(cands)`), lets headless branches rise upstream, excludes
loop-closing segments, accepts more fall than the nominal grade but never
less, and takes gradients from the line style name (`Pipe 1-80`). Heads come
from marker families carrying a typed `Invert Level`. Update Pipes re-curves
recorded pipes in place so tags keep their hosts (`:1030-1096`). Reason:
civil drainage scope; no generic tool models flow direction or merge
precedence.

**The invert-to-centreline convention, applied consistently.**
Revit pipes are centreline-defined; drainage data is invert-defined. The
half-diameter lift is applied in four places (`lib/pymep_dashboard_pipes.py:243`,
`lib/pymep_lines_to_pipes.py:1013`, `lib/pymep_connect_fixtures.py:266`,
`lib/pymep_drainage_networks.py:161`) and tested in
`tests/test_z_conventions.py`. Reason: domain convention a generic tool has
no notion of.

**Chamber placement with vertical-origin resolution and geometry QA.**
`lib/pymep_dashboard.py:251-378` places a throwaway instance, drives its
height and reads the box to learn whether the family is modelled from cover
down or sump up, then anchors accordingly; `:1005-1056` checks each placed
chamber against expected rim and sump and tells the family from the placement
apart. Reason: bespoke chamber families; project-specific.

**Civil 3D interchange.**
LandXML parsing with the `<Center>` = northing, easting convention and the
invert precedence rules lives in `dashboard/utilities3Dviewer.html:1180-1315`;
survey transform with explicit offsets first and model georeference second in
`lib/pymep_landxml_place2.py:96-228`. Reason: Civil 3D is outside Nexar's
world entirely.

**Conduit bank encasement.**
`conduit_analysis/` (CPython) clusters pipes and fittings into runs and
collections, sizes each collection to its largest OD, unifies cross-sections
along a run, and `08_Electrical.panel/00_Encasement` rebuilds it in Revit as
rectangular ducts with elbows whose radius comes from the pipe bend
(`lib/pymep_build.py`, `lib/pymep_connect.py:190-201`), cover on every side.
Reason: data-centre external containment; nobody else does this.

**Terrain-draped fence setting-out.**
`lib/pymep_fence_revit.py:1372-1783` solves corners as every intersection,
lets config priority win a corner, keeps foundation circles touching at
double posts, guarantees no bay exceeds the spacing, walks the perimeter to
number marks `FF1..FF7A1`, writes survey eastings and northings and a
user-written top-of-concrete equation onto each foundation
(`lib/pymep_fence.py:170-234`). Update Fence moves posts in place when the
count matches. Reason: setting-out, not drawing; 91 tests behind the maths.

**Kerbs that step or tilt with the ground.**
`lib/pymep_path.py:75-87` fits tilted units as hypotenuses so they still
touch on a slope; the flat variant steps. Reason: paving detail.

**Toposolid excavation by service envelope.**
`lib/pymep_topo_cut.py` projects each element's true silhouette with
`ExtrusionAnalyzer`, generates a void family in memory, loads it and cuts.
Reason: there is no public API to sketch a void into a Toposolid, and no
architectural tool needs this.

**Chamber drawing pipeline.**
Scope boxes rotated so the chamber face nearest up is on top, plan views with
exact crops, sections only on sides that cut pipework (`lib/pymep_section_cut.py`),
lettering by side, sheets by pattern, dimension strings from named reference
planes, all in one undo (`04_Chambers.panel`). Reason: a chamber convention
(`Mark` as key, `x/y/z` planes, `SIDE A..D`) that is yours alone.

**View template, filter, pattern, level and line-style transfer by name.**
`lib/pymep_vt_serialize.py`, `lib/pymep_vt_deserialize.py`,
`lib/pymep_vt_compat.py`: nested AND/OR filter rules, overrides, view range,
phase filter by name; per-item status rows; same-name items updated in place
so view assignments survive; a genuine pre-flight shopping list
(`pymep_vt_deserialize.py:924-1133`) and a name-mapping window. Reason: not
domain-specific, just better engineered than most commercial equivalents.
This is the one feature to lead with if pyMEP is ever shown to anyone.

**Manhole Plan export.**
`10_ManholePlan.panel/ExportManholes` reads the family's `a1..a7`, `b1..b7`,
`z1..z6` and conduit-boundary planes plus obstacle flags for an external web
tool. Reason: your own convention feeding your own tool.

---

## 3. Honest gaps

These are things Nexar advertises for which pyMEP has no answer. Not a partial
answer. None.

- **Tagging.** Not one `IndependentTag` in 41,000 lines. If a client wants
  pipe tags, room tags, or door tags placed automatically, pyMEP cannot help.
- **Schedules.** No creation, no editing, no export. Zero.
- **Excel round trip.** Zero.
- **COBie, Uniclass, OmniClass, Uniformat, MasterFormat.** Zero. For UK
  data-centre delivery this is the gap most likely to be exposed by a client's
  information requirements.
- **Data-standards rules with dry run and auto-fix.** Zero. Naming conventions
  in pyMEP are enforced by nothing.
- **Model health, warnings triage, purge, in-place family reporting.** Zero.
- **Clash detection.** Zero.
- **Scheduled or unattended runs, run history.** Zero. Every tool needs a
  human clicking a button and dismissing dialogs.
- **Cloud model awareness.** Zero, and worse than zero: per-project folders
  keyed on the model title will collide across projects.
- **Dimensioning anything that is not a chamber section.** Zero.
- **A support commitment, a compatibility statement, a changelog.** None
  exist. The README makes no Revit version claim at all.

Nexar's claims on every one of these are unverified. But an unverified claim
beats an absence.

---

## 4. Where pyMEP is worse at what it already attempts

This section judges the annotation and dimensioning code against Nexar's
reliability claims: batch failure handling, warning suppression, linked-model
transforms, collision avoidance. Line numbers are v1.260.0.

### Batch failure handling: fails the bar in the one place it says it passes

`06_Annotate.panel/00_Annotate.pushbutton/script.py:565-597` opens one
transaction, loops over every label, and only wraps the leader. The comment at
`:581-582` says "per-leader failure is swallowed so one bad bank doesn't roll
back the lot". `TextNote.Create` at `:570` is not wrapped. One bad type id,
one locked view, one over-long string, and `t.RollBack()` at `:593` discards
every label placed so far, then reports how many it placed before the error,
none of which exist any more. That is the exact scenario Nexar's "800
dimensions" line describes, and pyMEP fails it.

`06_DimensionSection` is better per string (`_make_string` and `chain` return
text rows on failure) but the whole multi-view run is one transaction
(`:1144-1158`). A failure at `Commit` loses every section. There is no
progress bar and no cancel in the script itself; only the pipeline supplies
one from outside.

Elsewhere in the same panel: `01_ChamberPlans:1571-1735`,
`02_CreateSections:1118-1169` and `03_SheetSetup:422-534` are all-or-nothing.
In a workshared model, one borrowed element on chamber 90 discards 89
successes.

### Warning suppression: the fix exists in the repo and is not used

`lib/pymep_replace_structure.py:168-190` has a correct `IFailuresPreprocessor`
that deletes warnings and a `_quiet(t)` helper that sets it with delayed mini
warnings. It is called from four places, none of them annotation or
dimensioning. So at every `Commit` in Annotate (`:591`), Pipe End Elev
(`:316`), Dia+Slope (`:205`) and Dimension Section (`:1155`), Revit's modal
warning dialog can appear: duplicate Mark, dimension not visible, text outside
crop. In the headless pipeline path (`00_SheetsPipeline:994` executing
`06_DimensionSection` via `exec` at `:850`) that dialog appears behind a
progress bar in an unattended four-step run. Cancel on the wrong button and
the batch rolls back.

### Linked models: invisible

No annotation or dimension code reads a `RevitLinkInstance`. Annotate takes
`uidoc.Selection` and buckets a link under "others" (`00_Annotate:140-157`).
Dimension Section collects with `FilteredElementCollector(doc, v.Id)` only
(`:212`, `:540`); a chamber in a link yields "no chamber in view" for every
rule. `Reference.CreateLinkReference` is absent from the whole repo, so even
if links were collected, nothing could dimension to them. The idiom for
reading links exists thirty lines away in `02_CreateSections:938-957` and was
never applied here.

### Collision avoidance: none, and repeat runs stack

Annotate offsets every label by one fixed model-space vector
(`00_Annotate:498-504`, `:552-559`); two banks with close centroids get
overlapping text. The offset constant is 500 mm in model space
(`lib/pymep_config.py:216`), not scale-aware: 10 mm on paper at 1:50, 1 mm at
1:500. Dimension Section is scale-aware (`lib/pymep_chamber_sections.py:610-628`)
but its slot counters reset every run (`06_DimensionSection:506`), so a second
run places string one exactly on top of the first run's string one. Nothing
reads existing annotation before placing.

### Specific defects found on the way

- **Silent wrong invert levels.** `02_PipeEndElev:290-303` parses the spot's
  display string with `re.search(r"-?\d+(?:\.\d+)?", ...)`, takes the first
  number, subtracts half the OD in millimetres, and writes the result back with
  `ValueOverride`. A thousands separator (`12,345.5`) matches `12`; a metre
  display gets 80 metres subtracted. No exception, no warning, a wrong level on
  a drawing. This is pure string code that could be unit-tested and is not.
- **Throwaway probe dimensions in loops.** `06_DimensionSection:741-759` tries
  every pair of planes with a created-then-deleted dimension when "dimension
  anyway" is on: up to n squared create and delete cycles per string per view,
  each able to raise an unsuppressed warning. `_plane_extent` adds a
  `doc.Regenerate()` inside its probe loop (`:407`).
- **Chamber found by "largest family instance by bounding-box diagonal"**
  (`:208-225`). A large neighbouring family wins and every rule reports the
  wrong object. Plane orientation is accepted only inside a hard-coded 0.99 dot
  product (`:375, 377, 641`), about eight degrees.
- **Deprecated id access in the file that defines the shim.**
  `06_DimensionSection:102-109` defines a `.Value` / `.IntegerValue` fallback
  and then uses raw `IntegerValue` at `:918, 921, 970, 1003, 1005, 1088`.
- **Associations lost on every update.** `lib/pymep_chamber_links.py:42-53`
  writes `chamber_section_links.json` under the extension folder. The updater
  merge-copies that folder to `%APPDATA%` and then deletes it
  (`lib/pymep_update.py:308-324`), but `links_path` never reads the durable
  home, so after an update Update Positions and Match Sections see an empty
  file. `tests/test_exports_home.py` and the README both describe a path this
  module does not use. A real project's links file is committed to the repo
  and ships inside every installer:
  `pyMEP.extension/exports/AMS01-EX-GLT-XX-EX-I-XXX-3DM-073001/chamber_section_links.json`.
- **Sheet layout ignores the title block.** `lib/pymep_sheet_setup.py:254`
  wraps against the raw sheet width; views land on a right-hand title strip;
  overflow is counted and reported, not corrected.
- **View-to-chamber binding is a name token.** `lib/pymep_sheet_setup.py:66-134`
  matches views to chambers by the Mark appearing in the view name and
  sections must end `SIDE <letter>`. Rename a view and the association is gone.

### The pattern behind all of it

Roughly 1,300 `except` clauses, about 39% of them a bare `pass`
(`lib/pymep_vt_serialize.py` is 69% silent). That is how pyMEP survives API
drift without crashing, and it is also how the next Revit change will produce
wrong geometry with a green summary instead of a stack trace. Nexar's copy
promises the opposite failure mode: loud per-item failures and a run that
continues. Whether they deliver it is unverified; pyMEP verifiably does not.

---

## 5. Build versus buy, per row

Context that drives every row: one maintainer, no CI test run (the 550 tests
pass in under a second and `.github/workflows/tag-release.yml` never invokes
them), about six releases per day at peak (17 on 4 September 2026), an update
button that installs the latest tag with no verification, and a Revit version
compatibility burden nobody else carries. Time is the scarce resource, not
the £30 a month.

| Row | Build, buy, or neither | Reasoning |
|---|---|---|
| Dimensioning of walls, grids, openings, elevations, 3D | **Buy, if you ever need it. Do not build.** | You would be building a generic product from scratch, on IronPython 2.7, alone, with 5% warning coverage. Nexar's version is unverified, but the trial costs nothing. Chamber sheets rarely need it, so the honest answer may be neither. |
| Chamber section dimensioning | **Keep building, but fix it before extending it.** | Nexar will never know your reference planes. The rule list and setting-out are the right shape. Apply `_quiet`, isolate per string, read linked chambers, stop probing with throwaway dimensions. |
| Tagging | **Buy.** | Zero in pyMEP, and it is not domain-specific. Placement with crowding avoidance is months of work to do properly. This is the first thing to test in a trial: tag pipes and conduits in your chamber plans and sections and see if the leaders are usable. |
| Schedules and Excel sync | **Buy, if verified.** | Zero in pyMEP. Not domain-specific. A two-way Excel sync done badly corrupts models, so test it on a copy. |
| Chamber sheet generation | **Keep. Fix margins and binding.** | Nexar's sheet generation is generic and will not lay out plan-plus-sections per chamber. Two afternoons fix the margin and overflow problems; extensible storage fixes the name-token binding. |
| Room data sheets | **Neither.** | Not your work. |
| COBie, Uniclass, classifications | **Buy, when a client requires it. Do not build.** | Region-aware picklists and validation are a product, not a button. If a data-centre client's exchange information requirements call for COBie, a trial is the cheapest way to find out whether Nexar's is credible. Unverified. |
| Data-standards rules with dry run and auto-fix | **Buy for the generic case; steal the pattern for your own conventions.** | Your conventions (`Mark` keys, `SIDE` suffixes, `pyMEP_Network`, `x/y/z` planes) will never be in Nexar's rule set. A small pyMEP checker that scans and reports, with a dry run, is a day's work and worth it. |
| Model health and cleanup | **Buy, if verified. Do not build.** | Zero in pyMEP, generic, and the "nightly headless" part needs infrastructure you do not have. |
| Clash detection | **Neither, probably.** | Navisworks or Revit's own interference check already exists; Nexar's "lightweight" version adds reports. Only worth it if the Excel switchback is actually good. |
| Reliability: warning preprocessors, per-item isolation | **Build. Non-negotiable. Not purchasable.** | Nothing Nexar sells makes pyMEP's own 74 transactions safer. Share `_quiet` from a lib module and call it in every transaction; that is a mechanical change across roughly 40 files and the single highest-value week available to this codebase. |
| Linked-model handling | **Build the helper once.** | Three copies of the same idiom exist. One `iter_model_elements(doc, include_links)` yielding element, transform and link, plus `CreateLinkReference` for dimensions, would lift 43 buttons at once. Nexar cannot help with pyMEP's tools. |
| Cloud model paths | **Build.** | Key per-project folders on the central model path GUID, not `doc.Title`. Small, urgent, and no vendor fixes it for you. |
| Scheduled or headless runs | **Build a thin version, later.** | Update Fence, Quick Merge and Sync Input Nodes already run without a dialog. `pyrevit run` can drive them from a scheduled task. Cheap once the reliability work is done; pointless before it. |
| Drawing-to-Revit (Nexar Plan) | **Neither.** | You convert LandXML and model lines into graded networks. DWG-to-ungraded-pipes would be a step backwards. £480 a year for nothing. |
| Revit 2025 to 2027 compatibility | **Build, and state it.** | Adopt `pymep_vt_compat` everywhere (about 100 `IntegerValue` sites), guard `Toposolid` and `UnitTypeId` imports, run the tests in CI, and write a supported-versions line in the README. Nobody sells you this. |
| Support SLA | **Neither.** | You are the SLA. A buyer of Nexar gets one for Nexar's features only. |

**Net recommendation.** Take the Nexar Docs trial and run three tests on a
copy of a live chamber model: (1) tag pipes and conduits in a chamber plan
and a section and judge leader placement against your drawing standard;
(2) build a chamber schedule, push it to Excel, edit it, push it back;
(3) if any client has asked for it, run the COBie or Uniclass export and hand
the output to whoever validates it. If two of three are credible, buy Docs as
a supplement at the single-user tier and stop thinking about building any of
those three. Do not buy Nexar Plan. Spend the week you save on the
reliability work in section 6.

---

## 6. Worth stealing regardless

Patterns from Nexar's feature list that would improve pyMEP whether or not a
penny is ever paid.

1. **A failure preprocessor on every transaction.** Move `_SwallowWarnings`
   and `_quiet` out of `lib/pymep_replace_structure.py` into
   `lib/pymep_revit.py` and call `_quiet(t)` at all 74 transaction sites.
   Log what was swallowed so it lands in the report.
2. **Per-item isolation with a failure manifest.** For every batch tool: one
   `TransactionGroup` for one undo, one sub-transaction or guarded block per
   element, a `failed` list with element id and reason, printed as a table and
   written to the run log. `06_DimensionSection` and `ImportProjectData`
   already do most of this; `00_Annotate`, `01_ChamberPlans`,
   `02_CreateSections` and `03_SheetSetup` do not.
3. **"Test on sample" and dry run everywhere.** The pre-flight in
   `lib/pymep_vt_deserialize.py` is the model. Chamber Plans, Create Sections
   and Sheet Setup should offer a report-only pass that says what would be
   created, renamed or skipped, before anything is transacted.
4. **Named presets as a general facility.** Dimension sets and fence configs
   prove the pattern. Generalise it: a preset for the whole Chambers dialog,
   a preset for the dashboard layer-to-workset mapping, stored per project
   rather than per user.
5. **Run history.** `lib/pymep_log.py` already writes a file per run for three
   commands. Extend it to every command and persist the report tables, so a
   question about last Tuesday's batch has an answer.
6. **Linked models as a first-class input.** One shared iterator, one shared
   transform application, `CreateLinkReference` for dimensions, and a note in
   every report saying how many links contributed and which were unloaded.
7. **A rule engine for your own data standards.** A small checker with
   compiled regex rules over selected categories (`Mark` present and unique,
   sections named `<Mark> SIDE <letter>`, chambers carrying `x1..` planes,
   `pyMEP_Network` populated), report first, auto-fix second, in one undo.
8. **Scheduled runs for the tools that already need no dialog.** Update Fence,
   Sync Input Nodes, Update Pipes and Quick Merge via `pyrevit run` from a
   nightly task on the coordination model, once item 1 makes them safe.
9. **A compatibility statement and a CI gate.** State the Revit versions
   supported, run the 550 tests on every push, add an IronPython 2.7 syntax
   check, and refuse to tag when they fail. Nexar's version list is marketing;
   yours would be true.
10. **Reports that leave the output window.** Nexar promises PDF and HTML
    reports and Excel switchback. The cheap version for pyMEP is writing every
    `print_table` to CSV alongside the log with element ids in the first
    column, so a reviewer can select the failures back in Revit.
