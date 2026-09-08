# -*- coding: utf-8 -*-
"""Export Manholes - the manhole family's named reference planes and
every placed instance's transform to a JSON file for the Manhole Plan
drawing board.

WHAT it writes (millimetres; the web tool's importer depends on the
shape, see pymep_manhole_export):
  * per family carrying the plane convention: the planes named a1-a7,
    b1-b7, z1-z6 and <axis>_conduit_boundary_1/2, each with its normal,
    its signed offset from the family origin along that normal and a
    point on it;
  * per placed instance: element id, Mark, type name, origin, family X
    and Y axes, rotation of the family X axis in project XY, mirrored.

WHERE it runs:
  * In a PROJECT: every distinct placed family is opened once with
    EditFamily (in-place and non-editable families skipped, the family
    document always closed without saving); the ones that carry at
    least one wanted plane are written with all their instances.
  * In a FAMILY document: that family's planes with an empty instance
    list.

Progress goes to the pyRevit output window; a closing alert gives the
family and instance counts and the file path, or says plainly that no
family carries the convention and names the expected planes.

Runs on Revit 2018-2026 under IronPython 2.7 and CPython 3: .format()
only, both unit APIs (UnitTypeId from 2021, DisplayUnitType before),
both ElementId shapes (.Value from 2024, .IntegerValue before), and
ReferencePlane.GetPlane() with a BubbleEnd / Normal fallback.
"""

__title__ = "Export\nManholes"
__author__ = "Glent Group"

import io
import os
import sys

for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

from pyrevit import revit, DB, forms, script

import pymep_json
import pymep_manhole_export as M

output = script.get_output()

try:
    doc = revit.doc
except Exception:
    doc = None
if doc is None:
    doc = __revit__.ActiveUIDocument.Document


# ---------------------------------------------------------------------------
# Version shims
# ---------------------------------------------------------------------------
_UNIT_ID = getattr(DB, "UnitTypeId", None)
if _UNIT_ID is not None:                      # Revit 2021+
    _MM = _UNIT_ID.Millimeters
else:                                         # Revit 2020 and earlier
    _MM = DB.DisplayUnitType.DUT_MILLIMETERS


def to_mm(v):
    try:
        return round(DB.UnitUtils.ConvertFromInternalUnits(v, _MM), 2)
    except Exception:
        return M.feet_to_mm(v)


def eid(element_id):
    v = getattr(element_id, "Value", None)
    return int(v) if v is not None else int(element_id.IntegerValue)


def _name(el):
    try:
        n = el.Name
        if n:
            return u"{0}".format(n)
    except Exception:
        pass
    try:
        return u"{0}".format(DB.Element.Name.__get__(el))
    except Exception:
        return u""


def _xyz(p):
    return (p.X, p.Y, p.Z)


def _plane_geometry(rp):
    # (origin, normal) of a reference plane: GetPlane() where the API has
    # it, the bubble end and Normal on older builds.
    try:
        pl = rp.GetPlane()
        return _xyz(pl.Origin), _xyz(pl.Normal)
    except AttributeError:
        return _xyz(rp.BubbleEnd), _xyz(rp.Normal)


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------
def plane_records(famdoc):
    out = []
    for rp in DB.FilteredElementCollector(famdoc).OfClass(DB.ReferencePlane):
        name = _name(rp)
        if not M.plane_wanted(name):
            continue
        try:
            origin, normal = _plane_geometry(rp)
        except Exception as ex:
            output.print_md("- plane '{0}': could not read its geometry "
                            "({1})".format(name, ex))
            continue
        out.append(M.plane_record(name, origin, normal, to_mm))
    return M.sort_planes(out)


def instance_record(inst):
    t = inst.GetTransform()
    mark = None
    try:
        p = inst.get_Parameter(DB.BuiltInParameter.ALL_MODEL_MARK)
        if p is not None and p.HasValue:
            mark = p.AsString()
    except Exception:
        mark = None
    try:
        mirrored = bool(inst.Mirrored)
    except Exception:
        mirrored = False
    return M.instance_record(eid(inst.Id), mark, _name(inst.Symbol),
                             _xyz(t.Origin), _xyz(t.BasisX), _xyz(t.BasisY),
                             mirrored, to_mm)


def _skip_reason(fam):
    # Why a family is not opened: in-place, not editable, or annotation.
    try:
        if fam.IsInPlace:
            return "in-place"
    except Exception:
        pass
    try:
        if not fam.IsEditable:
            return "not editable"
    except Exception:
        return "not editable"
    try:
        cat = fam.FamilyCategory
        if cat is not None and cat.CategoryType == DB.CategoryType.Annotation:
            return "annotation"
    except Exception:
        pass
    return None


def family_planes(fam):
    # The wanted planes of a loadable family, read from its own document
    # which is always closed again without saving.
    famdoc = doc.EditFamily(fam)
    try:
        return plane_records(famdoc)
    finally:
        try:
            famdoc.Close(False)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Collect
# ---------------------------------------------------------------------------
def collect_project():
    # {family id: record | None}, opening each distinct family once.
    instances = list(DB.FilteredElementCollector(doc)
                     .OfClass(DB.FamilyInstance)
                     .WhereElementIsNotElementType())
    by_family = {}
    order = []
    for inst in instances:
        try:
            fam = inst.Symbol.Family
            key = eid(fam.Id)
        except Exception:
            continue
        if key not in by_family:
            by_family[key] = (fam, [])
            order.append(key)
        by_family[key][1].append(inst)
    output.print_md("**{0}** placed instance(s) in **{1}** family(ies) - "
                    "opening each family once to look for the planes."
                    .format(len(instances), len(order)))
    found = []
    skipped = 0
    failed = 0
    for i, key in enumerate(order):
        fam, insts = by_family[key]
        fname = _name(fam)
        try:
            output.update_progress(i + 1, len(order))
        except Exception:
            pass
        why = _skip_reason(fam)
        if why:
            skipped += 1
            continue
        try:
            planes = family_planes(fam)
        except Exception as ex:
            failed += 1
            output.print_md("- '{0}': could not be opened ({1})".format(
                fname, ex))
            continue
        if not planes:
            continue
        records = []
        for inst in insts:
            try:
                records.append(instance_record(inst))
            except Exception as ex:
                output.print_md("- '{0}' instance {1}: skipped ({2})".format(
                    fname, eid(inst.Id), ex))
        output.print_md("- **{0}**: {1} plane(s), {2} instance(s)".format(
            fname, len(planes), len(records)))
        found.append(M.family_record(fname, planes, records))
    if skipped:
        output.print_md("- {0} family(ies) skipped (in-place, not editable "
                        "or annotation)".format(skipped))
    if failed:
        output.print_md("- {0} family(ies) could not be opened".format(failed))
    return found


def collect_family():
    planes = plane_records(doc)
    output.print_md("Family document **{0}**: {1} wanted plane(s).".format(
        _name_of_doc(), len(planes)))
    return [M.family_record(_name_of_doc(), planes, [])] if planes else []


def _name_of_doc():
    try:
        return u"{0}".format(doc.Title)
    except Exception:
        return u"family"


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
output.print_md("### Export Manholes")

try:
    model_path = doc.PathName or u""
except Exception:
    model_path = u""
init_dir = os.path.dirname(model_path) if model_path else ""
path = forms.save_file(file_ext="json",
                       default_name=M.default_file_name(_name_of_doc()),
                       init_dir=init_dir)
if not path:
    script.exit()
path = M.ensure_json_ext(path)

is_family = False
try:
    is_family = bool(doc.IsFamilyDocument)
except Exception:
    is_family = False

families = collect_family() if is_family else collect_project()
data = M.export_document(model_path or _name_of_doc(), families)
n_instances = sum(len(f["instances"]) for f in data["families"])

if data["families"]:
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(u"{0}".format(pymep_json.dumps(data, indent=2)))
    output.print_md("Written: `{0}`".format(path))
else:
    output.print_md("No family carries the plane convention - nothing "
                    "written. Expected planes: {0}.".format(
                        M.EXPECTED_PLANES))

forms.alert(M.summary_text(len(data["families"]), n_instances, path,
                           family_doc=is_family),
            title="Export Manholes")
