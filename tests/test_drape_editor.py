#!/usr/bin/env python3
"""Drape Floor's slab-shape editing, against a fake editor that behaves
the way Revit's does:

  * ResetSlabShape wipes the edits AND switches shape editing OFF,
    taking the vertices with it;
  * Enable switches it on; the corner vertices come back on the next
    Regenerate;
  * AddPoint is refused while shape editing is off.

The v1.264 script enabled first and reset second, which left every
floor with editing off: no corner lifted and every point refused
('the slab REJECTED every point'). prepare_editor must end ENABLED
with the vertices readable, whatever state the floor started in, and
PointAdder must cope with the add-a-point conventions seen across
Revit builds.

Run:  python3 tests/test_drape_editor.py
"""

import ast
import io
import math
import os
import unittest

SRC_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..",
    "pyMEP.extension", "pyMEP.tab", "03_Topography.panel",
    "02_DrapeFloor.pushbutton", "script.py")

WANT = ("fresh_editor", "enable_editor", "prepare_editor", "point_drawer",
        "vertex_modifier", "set_vertex_z", "delete_point", "PointAdder")


class XYZ(object):
    def __init__(self, x, y, z):
        self.X, self.Y, self.Z = x, y, z


ns = {"math": math, "XYZ": XYZ}
_src = io.open(SRC_PATH, encoding="utf-8").read()
for node in ast.parse(_src).body:
    if (isinstance(node, (ast.FunctionDef, ast.ClassDef))
            and node.name in WANT):
        exec(compile(ast.get_source_segment(_src, node), SRC_PATH,
                     "exec"), ns)
    elif isinstance(node, ast.Assign):
        try:
            exec(compile(ast.get_source_segment(_src, node), SRC_PATH,
                         "exec"), ns)
        except Exception:
            pass
for name in WANT:
    assert name in ns, name


# --------------------------------------------------------------- the fake
class Vertex(object):
    def __init__(self, x, y, z):
        self.Position = XYZ(x, y, z)


class Slab(object):
    """One floor's shape-edit state, shared by every editor read off it."""

    def __init__(self, top=0.0, enabled=False, relative=False,
                 refuse_off_face=False, modify_refuses=False,
                 reset_raises=False, spikes=0, legacy=False):
        self.top = top
        self.enabled = enabled
        self.relative = relative            # AddPoint Z measured from the slab
        self.refuse_off_face = refuse_off_face   # AddPoint only ON the face
        self.modify_refuses = modify_refuses
        self.reset_raises = reset_raises
        self.legacy = legacy
        self.corners = []
        self.added = [Vertex(1.0, 1.0, top + 5.0) for _ in range(spikes)]
        if enabled:
            self._corners()

    def _corners(self):
        self.corners = [Vertex(x, y, self.top)
                        for x, y in ((0, 0), (10, 0), (10, 10), (0, 10))]


class Editor(object):
    def __init__(self, slab):
        self.s = slab

    @property
    def IsEnabled(self):
        return self.s.enabled

    def Enable(self):
        self.s.enabled = True

    def ResetSlabShape(self):
        if self.s.reset_raises:
            raise RuntimeError("never edited")
        self.s.enabled = False
        self.s.corners = []
        self.s.added = []

    @property
    def SlabShapeVertices(self):
        return list(self.s.corners + self.s.added) if self.s.enabled else []

    def AddPoint(self, p):
        if not self.s.enabled:
            raise RuntimeError("Slab shape editing is not enabled.")
        if self.s.refuse_off_face and abs(p.Z - self.s.top) > 1e-6:
            raise ValueError("The point is not on the slab.")
        z = p.Z + (self.s.top if self.s.relative else 0.0)
        v = Vertex(p.X, p.Y, z)
        self.s.added.append(v)
        return v

    def ModifySubElement(self, v, delta):
        if self.s.modify_refuses:
            raise ValueError("cannot move this vertex")
        v.Position = XYZ(v.Position.X, v.Position.Y, v.Position.Z + delta)

    def DeletePoint(self, v):
        self.s.added.remove(v)


class LegacyEditor(Editor):
    """Revit 2023 and earlier: DrawPoint, no AddPoint."""

    def DrawPoint(self, p):
        return Editor.AddPoint(self, p)

    def __getattribute__(self, name):
        if name == "AddPoint":
            raise AttributeError(name)
        return object.__getattribute__(self, name)


class Floor(object):
    def __init__(self, slab):
        self.s = slab

    def GetSlabShapeEditor(self):
        if self.s.legacy:
            raise AttributeError("GetSlabShapeEditor")
        return Editor(self.s)

    @property
    def SlabShapeEditor(self):
        return LegacyEditor(self.s)


class Doc(object):
    def __init__(self, slab):
        self.s = slab

    def Regenerate(self):
        if self.s.enabled and not self.s.corners:
            self.s._corners()


def prepared(slab):
    ns["doc"] = Doc(slab)
    return ns["prepare_editor"](Floor(slab))


# --------------------------------------------------------------- tests
class PrepareEditor(unittest.TestCase):

    def test_fresh_flat_floor_ends_enabled_with_its_corners(self):
        slab = Slab()
        editor, facts = prepared(slab)
        self.assertTrue(facts["enabled"])
        self.assertEqual(facts["vertices"], 4)
        self.assertTrue(facts["reset"])
        self.assertEqual(len(list(editor.SlabShapeVertices)), 4)

    def test_previously_draped_floor_is_wiped_then_enabled(self):
        # the case the old enable-then-reset order broke: the reset
        # switched editing off after it had been switched on
        slab = Slab(enabled=True, spikes=3)
        editor, facts = prepared(slab)
        self.assertTrue(facts["enabled"])
        self.assertEqual(slab.added, [])            # old spikes gone
        self.assertEqual(facts["vertices"], 4)      # corners readable
        editor.AddPoint(XYZ(5, 5, 2.0))             # and points accepted

    def test_never_edited_floor_whose_reset_raises(self):
        slab = Slab(reset_raises=True)
        _editor, facts = prepared(slab)
        self.assertFalse(facts["reset"])
        self.assertTrue(facts["enabled"])
        self.assertEqual(facts["vertices"], 4)

    def test_legacy_property_editor(self):
        slab = Slab(legacy=True)
        editor, facts = prepared(slab)
        self.assertTrue(facts["enabled"])
        self.assertIsInstance(editor, LegacyEditor)
        draw = ns["point_drawer"](editor)
        v = draw(XYZ(2, 2, 1.5))
        self.assertAlmostEqual(v.Position.Z, 1.5)


def adder_for(slab, say=None):
    editor, _facts = prepared(slab)
    face_z = list(editor.SlabShapeVertices)[0].Position.Z
    return ns["PointAdder"](Floor(slab), editor, ns["point_drawer"](editor),
                            ns["vertex_modifier"](editor), face_z,
                            ns["doc"].Regenerate, say)


class PointAdderModes(unittest.TestCase):

    def test_absolute_build(self):
        slab = Slab()
        a = adder_for(slab)
        self.assertTrue(a.add(3, 3, 17.5))
        self.assertTrue(a.add(4, 4, 16.0))
        self.assertEqual(a.good, "abs")
        self.assertEqual([round(v.Position.Z, 6) for v in slab.added],
                         [17.5, 16.0])

    def test_build_measuring_z_from_the_slab_is_compensated(self):
        said = []
        slab = Slab(top=1.0, relative=True)
        a = adder_for(slab, said.append)
        self.assertTrue(a.add(3, 3, 17.5))      # lands at 18.5, corrected
        self.assertTrue(a.add(4, 4, 16.0))      # compensated up front
        self.assertEqual([round(v.Position.Z, 6) for v in slab.added],
                         [17.5, 16.0])
        self.assertTrue(any("SLAB" in m for m in said))

    def test_points_refused_off_the_face_are_added_on_it_and_lifted(self):
        said = []
        slab = Slab(refuse_off_face=True)
        a = adder_for(slab, said.append)
        self.assertTrue(a.add(3, 3, 17.5))
        self.assertTrue(a.add(4, 4, 15.25))
        self.assertEqual(a.good, "face")
        self.assertEqual([round(v.Position.Z, 6) for v in slab.added],
                         [17.5, 15.25])
        self.assertIn("not on the slab", a.first_error)
        self.assertTrue(any("lifted" in m for m in said))

    def test_a_point_that_will_not_lift_is_removed_not_left_as_a_spike(self):
        slab = Slab(refuse_off_face=True, modify_refuses=True)
        a = adder_for(slab)
        self.assertFalse(a.add(3, 3, 17.5))
        self.assertEqual(slab.added, [])        # nothing left at the level
        self.assertIsNone(a.good)
        self.assertIsNotNone(a.first_error)

    def test_editing_switched_off_underneath_is_switched_back_on(self):
        said = []
        slab = Slab()
        a = adder_for(slab, said.append)
        slab.enabled = False                    # something switched it off
        self.assertTrue(a.add(3, 3, 12.0))
        self.assertTrue(slab.enabled)
        self.assertTrue(any("switched back on" in m for m in said))
        self.assertIn("not enabled", a.first_error)

    def test_known_good_way_is_not_second_guessed(self):
        slab = Slab()
        a = adder_for(slab)
        self.assertTrue(a.add(3, 3, 12.0))
        self.assertEqual(a.good, "abs")
        slab.refuse_off_face = True             # this ONE point refused
        before = len(slab.added)
        self.assertFalse(a.add(4, 4, 13.0))
        self.assertEqual(len(slab.added), before)   # no face-mode junk
        self.assertEqual(a.good, "abs")

    def test_without_modify_only_the_absolute_way_is_tried(self):
        slab = Slab(refuse_off_face=True)
        editor, _f = prepared(slab)
        a = ns["PointAdder"](Floor(slab), editor, ns["point_drawer"](editor),
                             None, 0.0, ns["doc"].Regenerate)
        self.assertEqual(a.modes, ["abs"])
        self.assertFalse(a.add(3, 3, 12.0))


if __name__ == "__main__":
    unittest.main()
