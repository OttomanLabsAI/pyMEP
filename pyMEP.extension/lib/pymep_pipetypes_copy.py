# -*- coding: utf-8 -*-
"""Copy MEP types directly from another .rvt into the active model.

The direct path for same-version (or older-file-into-newer-Revit)
transfers: the source model is opened INVISIBLY in the background
(detached from central when workshared, so nothing can touch the real
file), the chosen types - across every importable category present
(pipe types, piping system types, pipe segments, duct types, duct
system types, cable tray types, conduit types) - are copied across with
``ElementTransformUtils.CopyElements``, which brings their routing
preferences and every dependent element (segments, schedules,
materials, fitting families) exactly like Transfer Project Standards,
and the source is closed without saving.

What this CANNOT do: read a file saved in a NEWER Revit than the one
running - Revit itself refuses to open those, so that direction has no
import path.

Name collisions: types (of any kind) that already exist in the active
model are kept - the copy uses ``DuplicateTypeAction.UseDestinationTypes``
so nothing is silently renamed to ``name 2``. The report says, per
category, which types came in new and which were already present.

IronPython 2.7 / Revit 2021-2026 safe.
"""

import clr
clr.AddReference("RevitAPI")

from System.Collections.Generic import List

from Autodesk.Revit.DB import (
    FilteredElementCollector, ElementId, ElementTransformUtils, Transform,
    Transaction, CopyPasteOptions, IDuplicateTypeNamesHandler,
    DuplicateTypeAction, ModelPathUtils, OpenOptions,
    DetachFromCentralOption,
)

from pymep_revit import safe_name


# ---------------------------------------------------------------------------
# pure data shaping (stdlib only - unit-tested without Revit)
# ---------------------------------------------------------------------------
def diff_names(before, after, requested):
    """What actually happened, from the pipe-type name sets BEFORE and
    AFTER the copy plus the requested names: ``(created, existed)`` -
    ``created`` is every genuinely new name, ``existed`` the requested
    ones that were already in the destination (kept, not overwritten)."""
    before = set(before)
    created = sorted(n for n in set(after) - before)
    existed = sorted(n for n in requested if n in before)
    return created, existed


# ---------------------------------------------------------------------------
# Revit API access
# ---------------------------------------------------------------------------
def _type_classes():
    """[(category label, API class)] for every importable MEP type
    category this Revit exposes - resolved defensively so a build
    without, say, cable trays just drops that category."""
    out = []

    def add(label, module, cls_name):
        try:
            mod = __import__(module, fromlist=[cls_name])
            cls = getattr(mod, cls_name, None)
            if cls is not None:
                out.append((label, cls))
        except Exception:
            pass

    add("Pipe Types", "Autodesk.Revit.DB.Plumbing", "PipeType")
    add("Piping System Types", "Autodesk.Revit.DB.Plumbing",
        "PipingSystemType")
    add("Pipe Segments", "Autodesk.Revit.DB.Plumbing", "PipeSegment")
    add("Duct Types", "Autodesk.Revit.DB.Mechanical", "DuctType")
    add("Duct System Types", "Autodesk.Revit.DB.Mechanical",
        "MechanicalSystemType")
    add("Cable Tray Types", "Autodesk.Revit.DB.Electrical",
        "CableTrayType")
    add("Conduit Types", "Autodesk.Revit.DB.Electrical", "ConduitType")
    return out


def list_types_by_category(doc):
    """[(category label, name, element), ...] for every type in the
    model across the importable categories, sorted category then name.
    Categories with nothing in the model simply don't appear."""
    out = []
    for label, cls in _type_classes():
        for el in FilteredElementCollector(doc).OfClass(cls):
            try:
                out.append((label, safe_name(el), el))
            except Exception:
                continue
    out.sort(key=lambda t: (t[0].lower(), t[1].lower()))
    return out


def _names_by_label(doc):
    """{category label: set of type names} - the before/after snapshots
    the created/kept report is diffed from."""
    snap = {}
    for label, name, _el in list_types_by_category(doc):
        snap.setdefault(label, set()).add(name)
    return snap


def open_source_document(app, path):
    """Open ``path`` invisibly in the background. Workshared files are
    detached (preserving worksets) so the central is never touched; the
    option is ignored for non-workshared files. Raises whatever Revit
    raises - the caller turns 'saved in a later version' into guidance."""
    mp = ModelPathUtils.ConvertUserVisiblePathToModelPath(path)
    opts = OpenOptions()
    try:
        opts.DetachFromCentralOption = \
            DetachFromCentralOption.DetachAndPreserveWorksets
    except Exception:
        pass
    opts.Audit = False
    return app.OpenDocumentFile(mp, opts)


class _UseDestinationTypes(IDuplicateTypeNamesHandler):
    """Keep the active model's types on any name collision - imports
    must never fork 'name 2' duplicates of segments/schedules/fittings
    that are already here."""

    def OnDuplicateTypeNamesFound(self, args):
        return DuplicateTypeAction.UseDestinationTypes


def copy_types(src_doc, dest_doc, picks, log=None):
    """Copy the chosen types into ``dest_doc`` with all their dependents,
    in one transaction.

    ``picks``: [(category label, name, element OF src_doc), ...] - the
    tuples ``list_types_by_category`` yields, filtered to the user's
    selection. Returns ``{category label: (created_names, kept_names)}``.
    Existing types are kept (UseDestinationTypes), never overwritten."""
    ids = List[ElementId]()
    requested = {}          # label -> [names]
    for label, name, el in picks:
        ids.Add(el.Id)
        requested.setdefault(label, []).append(name)

    before = _names_by_label(dest_doc)

    co = CopyPasteOptions()
    co.SetDuplicateTypeNamesHandler(_UseDestinationTypes())

    t = Transaction(dest_doc, "Import MEP types from RVT")
    t.Start()
    try:
        ElementTransformUtils.CopyElements(
            src_doc, ids, dest_doc, Transform.Identity, co)
        t.Commit()
    except Exception:
        t.RollBack()
        raise

    after = _names_by_label(dest_doc)
    report = {}
    for label, names in requested.items():
        created, existed = diff_names(
            before.get(label, set()), after.get(label, set()), names)
        report[label] = (created, existed)
        if log is not None:
            log("**{}**".format(label))
            for n in created:
                log("  + {}".format(n))
            for n in existed:
                log("  = {} (already in this model - kept)".format(n))
    return report
