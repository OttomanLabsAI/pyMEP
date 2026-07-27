# -*- coding: utf-8 -*-
"""Connect Fixtures - select ONE main pipe and any number of plumbing
fixtures, and each fixture gets: a vertical downpipe from its outlet
connector, an elbow, a sloped branch falling at 1:n toward the main, and
a takeoff fitting where it meets it.

One proper dialog drives it: branch diameter (mm, default = the fixture
outlet size, snapped to the main type's routing sizes), the slope ratio
1:n, and the upstream invert - by default it stays where the model
currently puts it (the branch meets the main as it lies and the elbow
level derives back up the slope); type an absolute level to fix it
instead. Branches take the main's pipe type, system type and level.
Diameter and slope are remembered between runs.
"""

__title__  = "Connect\nFixtures"
__author__ = "Glent Group"

import os
import sys

for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

from pyrevit import revit, forms, script

from pymep_connect_fixtures import (
    fixture_outlet_info, main_pipe_info, branch_points,
    connect_fixture_to_main,
)
from pymep_gully_connect import _snap_dia_ft
from pymep_config import load_settings, save_settings
from pymep_revit import safe_name, ft2mm, mm2ft
from pymep_log import Logger

import clr
clr.AddReference("RevitAPI")
from Autodesk.Revit.DB import FamilyInstance
from Autodesk.Revit.DB.Plumbing import Pipe

output = script.get_output()
log = Logger(output, "ConnectFixtures")
doc = revit.doc
uidoc = revit.uidoc

log("### Connect Fixtures to Main")

# ---------------------------------------------------------------------------
# 1. Partition the selection: exactly one pipe + 1+ fixtures
# ---------------------------------------------------------------------------
mains = []
fixtures = []
for eid in uidoc.Selection.GetElementIds():
    el = doc.GetElement(eid)
    if isinstance(el, Pipe):
        mains.append(el)
    elif isinstance(el, FamilyInstance):
        o, _d = fixture_outlet_info(el)
        if o is not None:
            fixtures.append(el)

if len(mains) != 1 or not fixtures:
    forms.alert(
        "Select ONE pipe (the main run) plus the plumbing fixtures to "
        "connect into it, then run this again.\n\n"
        "Selected now: {} pipe(s), {} fixture(s) with an outlet "
        "connector.".format(len(mains), len(fixtures)),
        exitscript=True)

main = mains[0]
try:
    a, b, _tid, _sid, _lid, main_dia_ft = main_pipe_info(main)
except Exception as ex:
    log("{}".format(ex))
    log.close()
    forms.alert(str(ex), exitscript=True)

log("Main: **{}** ({:.0f} mm), fixtures: **{}**".format(
    safe_name(main), ft2mm(main_dia_ft), len(fixtures)))

# ---------------------------------------------------------------------------
# 2. Dialog defaults: outlet size, remembered slope, current invert
# ---------------------------------------------------------------------------
settings = load_settings()
first_outlet, first_dia_mm = fixture_outlet_info(fixtures[0])
def_dia = settings.get("fixture_branch_dia_mm") or first_dia_mm or 100.0
def_slope = settings.get("fixture_branch_slope") or 100.0

pipe_type = doc.GetElement(main.GetTypeId())
_dia_ft0 = _snap_dia_ft(doc, pipe_type, mm2ft(float(def_dia)))
_auto = branch_points(first_outlet, a, b, float(def_slope), _dia_ft0)
def_invert = _auto["upstream_invert_m"]

XAML_PATH = os.path.join(
    os.path.dirname(os.path.abspath(sys.modules["pymep_config"].__file__)),
    "pymep_connect_fixtures.xaml")


class ConnectWindow(forms.WPFWindow):

    def __init__(self):
        forms.WPFWindow.__init__(self, XAML_PATH)
        self.result = None
        self.TxtInfo.Text = ("{} fixture(s) -> main '{}' ({:.0f} mm)"
                             .format(len(fixtures), safe_name(main),
                                     ft2mm(main_dia_ft)))
        self.TxtDia.Text = "{:.0f}".format(float(def_dia))
        self.TxtSlope.Text = "{:g}".format(float(def_slope))
        self.TxtInvert.Text = "{:.3f}".format(def_invert)

    def on_auto(self, sender, args):
        try:
            self.TxtInvert.IsEnabled = not self.ChkAuto.IsChecked
        except Exception:
            pass

    def on_connect(self, sender, args):
        try:
            dia = float(self.TxtDia.Text)
            slope = float(self.TxtSlope.Text)
            if dia <= 0 or slope <= 0:
                raise ValueError()
        except Exception:
            self.StatusText.Text = ("Diameter and slope must be positive "
                                    "numbers.")
            return
        inv = None
        if not self.ChkAuto.IsChecked:
            try:
                inv = float(self.TxtInvert.Text)
            except Exception:
                self.StatusText.Text = ("Type the upstream invert level in "
                                        "metres, or tick 'keep it where it "
                                        "currently is'.")
                return
        self.result = {"dia_mm": dia, "slope": slope, "invert_m": inv}
        self.Close()

    def on_cancel(self, sender, args):
        self.result = None
        self.Close()


win = ConnectWindow()
win.ShowDialog()
if win.result is None:
    log("Cancelled.")
    log.close()
    script.exit()
res = win.result

settings["fixture_branch_dia_mm"] = res["dia_mm"]
settings["fixture_branch_slope"] = res["slope"]
try:
    save_settings(settings)
except Exception:
    pass

log("Branch dia **{:.0f} mm**, slope **1:{:g}**, upstream invert: {}"
    .format(res["dia_mm"], res["slope"],
            "keep current (meet the main as it lies)"
            if res["invert_m"] is None
            else "**{:.3f} m** (fixed)".format(res["invert_m"])))

# ---------------------------------------------------------------------------
# 3. Build each branch (each in its own transaction)
# ---------------------------------------------------------------------------
done = 0
failed = 0
fitting_misses = 0
for fx in fixtures:
    log("**{}**:".format(safe_name(fx)))
    try:
        r = connect_fixture_to_main(doc, fx, main, res["slope"],
                                    res["dia_mm"],
                                    invert_m=res["invert_m"], log=log)
        done += 1
        fitting_misses += r["fitting_misses"]
    except Exception as ex:
        failed += 1
        import traceback
        log(traceback.format_exc())
        log("  ! not connected: {}".format(ex))

log("#### Summary")
log("- Fixtures connected: **{}**".format(done))
if fitting_misses:
    log("- Fitting(s) that could not be placed: **{}** (pipes are in - "
        "see the notes above)".format(fitting_misses))
if failed:
    log("- Failed (nothing created for them): **{}**".format(failed))
log.close()
