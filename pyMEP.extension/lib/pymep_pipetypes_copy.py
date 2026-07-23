# -*- coding: utf-8 -*-
"""Copy pipe types directly from another .rvt into the active model.

The direct path for same-version (or older-file-into-newer-Revit)
transfers: the source model is opened INVISIBLY in the background
(detached from central when workshared, so nothing can touch the real
file), the picked ``PipeType`` elements are copied across with
``ElementTransformUtils.CopyElements`` - which brings their routing
preferences and every dependent element (pipe segments, schedules,
materials, fitting families) exactly like Transfer Project Standards -
and the source is closed without saving.

What this CANNOT do: read a file saved in a NEWER Revit than the one
running - Revit itself refuses to open those. That direction is what the
Export Pipe Types JSON is for (export on the newer machine, rebuild from
JSON on the older one).

Name collisions: types (of any kind) that already exist in the active
model are kept - the copy uses ``DuplicateTypeAction.UseDestinationTypes``
so nothing is silently renamed to ``name 2``. The report says which pipe
types came in new and which were already present.

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
from Autodesk.Revit.DB.Plumbing import PipeType

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


def existing_pipe_type_names(doc):
    """set of the model's pipe type names."""
    out = set()
    for pt in FilteredElementCollector(doc).OfClass(PipeType):
        try:
            out.add(safe_name(pt))
        except Exception:
            continue
    return out


class _UseDestinationTypes(IDuplicateTypeNamesHandler):
    """Keep the active model's types on any name collision - imports
    must never fork 'name 2' duplicates of segments/schedules/fittings
    that are already here."""

    def OnDuplicateTypeNamesFound(self, args):
        return DuplicateTypeAction.UseDestinationTypes


def copy_pipe_types(src_doc, dest_doc, pipe_types, log=None):
    """Copy ``pipe_types`` (PipeType elements OF ``src_doc``) into
    ``dest_doc`` with all their dependents, in one transaction.
    Returns ``(created_names, existed_names)``."""
    requested = []
    ids = List[ElementId]()
    for pt in pipe_types:
        requested.append(safe_name(pt))
        ids.Add(pt.Id)

    before = existing_pipe_type_names(dest_doc)

    co = CopyPasteOptions()
    co.SetDuplicateTypeNamesHandler(_UseDestinationTypes())

    t = Transaction(dest_doc, "Import pipe types from RVT")
    t.Start()
    try:
        ElementTransformUtils.CopyElements(
            src_doc, ids, dest_doc, Transform.Identity, co)
        t.Commit()
    except Exception:
        t.RollBack()
        raise

    after = existing_pipe_type_names(dest_doc)
    created, existed = diff_names(before, after, requested)
    if log is not None:
        for n in created:
            log("  + {}".format(n))
        for n in existed:
            log("  = {} (already in this model - kept)".format(n))
    return created, existed
