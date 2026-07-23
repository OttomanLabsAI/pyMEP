#!/usr/bin/env python3
"""Round-trip tests for the survey->internal transform (stdlib only,
no Revit needed).

The bug this guards against: ``ProjectPosition.Angle`` describes the
INTERNAL->SHARED rotation, but ``make_survey_fn`` rotates survey deltas
INTO the internal frame - so the model-coordinates path must use the
NEGATED angle. Feeding +Angle straight in (the old behaviour) rotated
the whole site by 2*Angle round the internal origin: structures landed
outside the site boundary in the wrong orientation, exactly what the
screenshot showed.

The fix measures the internal->shared rotation with a GetProjectPosition
probe point on the internal +X axis (same self-verification idea as the
proven DUB41 toposolid script), so even a Revit build whose Angle sign
convention differs cannot break it. These tests simulate a georeferenced
model with BOTH sign conventions for the reported Angle and require the
identical (correct) result from each.

Also guarded: pymep_dashboard.solve_points' model candidate must keep
the SETTINGS Z offset (the pipe placer keeps Settings off_z on its model
transform, so structures and pipes must share one vertical datum - the
"structures and pipes are coming in at different levels" screenshot).

The functions are extracted from their modules by AST (the modules
import the Revit API and cannot be imported under CPython).

Run:  python3 tests/test_survey_rotation.py
"""

import ast
import math
import os
import sys
import types
import unittest

LIB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "..", "pyMEP.extension", "lib")

FT = 0.3048


# ---------------------------------------------------------------------------
# minimal Revit stand-ins
# ---------------------------------------------------------------------------
class XYZ(object):
    Zero = None

    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.X, self.Y, self.Z = float(x), float(y), float(z)

    def __repr__(self):
        return "XYZ({:.4f}, {:.4f}, {:.4f})".format(self.X, self.Y, self.Z)


XYZ.Zero = XYZ()

# fake Autodesk.Revit.DB so model_survey_position's inline import works
_db = types.ModuleType("Autodesk.Revit.DB")
_db.XYZ = XYZ
_revit = types.ModuleType("Autodesk.Revit")
_revit.DB = _db
_autodesk = types.ModuleType("Autodesk")
_autodesk.Revit = _revit
sys.modules["Autodesk"] = _autodesk
sys.modules["Autodesk.Revit"] = _revit
sys.modules["Autodesk.Revit.DB"] = _db


class Pos(object):
    def __init__(self, ew, ns, elev, angle):
        self.EastWest, self.NorthSouth = ew, ns
        self.Elevation, self.Angle = elev, angle


class FakeLocation(object):
    """Georeferenced model: internal->shared is R(theta_fwd) + origin,
    all in feet. ``reported_angle`` is what ProjectPosition.Angle claims,
    which the probe-based measurement must be immune to."""

    def __init__(self, theta_fwd, oe_ft, on_ft, elev_ft, reported_angle):
        self._th = theta_fwd
        self._oe, self._on, self._el = oe_ft, on_ft, elev_ft
        self._rep = reported_angle

    def GetProjectPosition(self, pt):
        c, s = math.cos(self._th), math.sin(self._th)
        return Pos(self._oe + pt.X * c - pt.Y * s,
                   self._on + pt.X * s + pt.Y * c,
                   self._el + pt.Z, self._rep)


class FakeDoc(object):
    def __init__(self, loc):
        self.ActiveProjectLocation = loc


def shared_m(loc, x_ft, y_ft, z_ft):
    """Ground truth: survey coordinates (metres) of an internal point."""
    p = loc.GetProjectPosition(XYZ(x_ft, y_ft, z_ft))
    return (p.EastWest * FT, p.NorthSouth * FT, p.Elevation * FT)


# ---------------------------------------------------------------------------
# AST extraction (with injected globals - the functions use math/XYZ)
# ---------------------------------------------------------------------------
def extract_function(module_file, func_name, extra=None):
    path = os.path.join(LIB, module_file)
    with open(path) as f:
        src = f.read()
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            ns = {"math": math, "XYZ": XYZ}
            ns.update(extra or {})
            exec(compile(ast.get_source_segment(src, node), path, "exec"),
                 ns)
            return ns[func_name]
    raise AssertionError("{} not found in {}".format(func_name, path))


make_survey_fn = extract_function("pymep_landxml_place2.py",
                                  "make_survey_fn")
model_survey_position = extract_function("pymep_landxml_place2.py",
                                         "model_survey_position")

# solve_points needs its module-mates and two inline imports faked
_fake_place2 = types.ModuleType("pymep_landxml_place2")
_fake_place2.model_survey_position = model_survey_position
sys.modules["pymep_landxml_place2"] = _fake_place2

SETTINGS = (3400000.0, 5500000.0, 45.667, 12.5)  # deliberately another site
_fake_cfg = types.ModuleType("pymep_config")
_fake_cfg.get_landxml_survey_transform = lambda: SETTINGS
sys.modules["pymep_config"] = _fake_cfg

solve_points = extract_function(
    "pymep_dashboard.py", "solve_points",
    extra={"_say": lambda log, msg: None, "_LIMIT_FT": 52500.0,
           "HEL18_OFF_E_M": SETTINGS[0], "HEL18_OFF_N_M": SETTINGS[1],
           "HEL18_OFF_Z_M": SETTINGS[2], "HEL18_ROT_DEG": SETTINGS[3]})


# ---------------------------------------------------------------------------
# the site used everywhere below (HEL18-like numbers)
# ---------------------------------------------------------------------------
THETA_FWD = math.radians(-40.36)          # true internal->shared rotation
ORIGIN_E_FT = 3498151.6589 / FT
ORIGIN_N_FT = 5554088.8918 / FT
ELEV_FT = 45.667 / FT
INTERNAL_TRUTH = [(1000.0, 0.0), (0.0, 2000.0), (-500.0, 700.0),
                  (2500.0, -1200.0)]


class MeasuredRotation(unittest.TestCase):
    """model_survey_position must return the SHARED->INTERNAL angle, and
    must measure it - whatever sign convention .Angle reports."""

    def _check(self, theta_fwd, reported_angle):
        loc = FakeLocation(theta_fwd, ORIGIN_E_FT, ORIGIN_N_FT, ELEV_FT,
                           reported_angle)
        mp = model_survey_position(FakeDoc(loc))
        self.assertIsNotNone(mp)
        self.assertAlmostEqual(mp[0], ORIGIN_E_FT * FT, places=6)
        self.assertAlmostEqual(mp[1], ORIGIN_N_FT * FT, places=6)
        self.assertAlmostEqual(mp[2], -math.degrees(theta_fwd), places=9)
        # round trip: internal truth -> shared metres -> back to internal
        fn = make_survey_fn(mp[0], mp[1], mp[2], ELEV_FT * FT)
        for x_ft, y_ft in INTERNAL_TRUTH:
            e_m, n_m, z_m = shared_m(loc, x_ft, y_ft, 33.0)
            p = fn(e_m, n_m, z_m)
            self.assertAlmostEqual(p.X, x_ft, places=5)
            self.assertAlmostEqual(p.Y, y_ft, places=5)
            self.assertAlmostEqual(p.Z, 33.0, places=5)

    def test_hel18_like_angle_both_reported_signs(self):
        self._check(THETA_FWD, THETA_FWD)
        self._check(THETA_FWD, -THETA_FWD)   # lying Angle: probe must win

    def test_positive_zero_and_odd_angles(self):
        for th in (math.radians(40.36), 0.0, math.radians(137.0)):
            self._check(th, th)
            self._check(th, -th)

    def test_old_plus_angle_behaviour_was_wrong(self):
        loc = FakeLocation(THETA_FWD, ORIGIN_E_FT, ORIGIN_N_FT, ELEV_FT,
                           THETA_FWD)
        # the pre-fix formula: rot_deg = +degrees(pos.Angle)
        fn_old = make_survey_fn(ORIGIN_E_FT * FT, ORIGIN_N_FT * FT,
                                math.degrees(THETA_FWD), ELEV_FT * FT)
        e_m, n_m, z_m = shared_m(loc, 1000.0, 0.0, 0.0)
        p = fn_old(e_m, n_m, z_m)
        err_ft = math.hypot(p.X - 1000.0, p.Y - 0.0)
        # 2*40.36 deg swing on a 1000 ft arm ~ 1290 ft off
        self.assertGreater(err_ft, 1000.0)


class SolvePointsModelCandidate(unittest.TestCase):
    """The dashboard structure solver, model-coordinates path."""

    def _solve(self):
        loc = FakeLocation(THETA_FWD, ORIGIN_E_FT, ORIGIN_N_FT, ELEV_FT,
                           THETA_FWD)
        rows = []
        for x_ft, y_ft in INTERNAL_TRUTH:
            e_m, n_m, _ = shared_m(loc, x_ft, y_ft, 0.0)
            rows.append({"x": e_m, "y": n_m, "z_m": 12.0})
        return solve_points(FakeDoc(loc), rows, prefer_model=True), loc

    def test_lands_on_internal_truth(self):
        (pts, mode, offs), loc = self._solve()
        self.assertEqual(mode, "model project position")
        self.assertAlmostEqual(offs[3], -math.degrees(THETA_FWD), places=9)
        for (p, pz, r), (x_ft, y_ft) in zip(pts, INTERNAL_TRUTH):
            self.assertAlmostEqual(p.X, x_ft, places=5)
            self.assertAlmostEqual(p.Y, y_ft, places=5)

    def test_z_datum_is_settings_not_model_elevation(self):
        (pts, mode, offs), loc = self._solve()
        # offs[2] must be the SETTINGS Z (pipes use it too) - NOT the
        # model's survey elevation, which put structures ~45 m under the
        # pipes when the two happened to differ
        self.assertAlmostEqual(offs[2], SETTINGS[2], places=9)
        for (p, pz, r) in pts:
            self.assertAlmostEqual(pz, (12.0 - SETTINGS[2]) / FT, places=6)

    def test_instance_rotation_base_matches_frame(self):
        """rot_base = radians(offs[3]) must map a world-frame bearing onto
        the same feature's internal-frame bearing (the RotateElement
        formula rot_base + row rot_deg)."""
        (pts, mode, offs), loc = self._solve()
        phi = math.radians(25.0)              # a world-frame direction
        a = shared_m(loc, 0.0, 0.0, 0.0)
        step = 100.0                          # metres along that bearing
        e2, n2 = a[0] + step * math.cos(phi), a[1] + step * math.sin(phi)
        fn = make_survey_fn(offs[0], offs[1], offs[3], offs[2])
        p1 = fn(a[0], a[1], 0.0)
        p2 = fn(e2, n2, 0.0)
        got = math.atan2(p2.Y - p1.Y, p2.X - p1.X)
        want = phi + math.radians(offs[3])
        diff = (got - want + math.pi) % (2 * math.pi) - math.pi
        self.assertAlmostEqual(diff, 0.0, places=9)


if __name__ == "__main__":
    unittest.main(verbosity=2)
