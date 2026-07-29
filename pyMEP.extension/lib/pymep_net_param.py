# -*- coding: utf-8 -*-
"""The pyMEP_Network parameter - every pipe, fitting and node a network
owns carries its network name as a real Revit instance parameter.

``ensure_network_param`` binds a shared TEXT parameter (fixed GUID, so
every project shares the same definition) to pipes, pipe fittings /
accessories and the usual node categories, under Identity Data.
``stamp_network`` writes a network name onto elements;
``with_connected_fittings`` widens a stamp to the fittings Revit
inserted by itself (couplings, reducers) by walking the pipes'
connectors; ``collect_by_network`` reads the whole model back as
{network: [elements]} - how the dashboard maps networks and how
manually drawn pipework joins or leaves one (just edit the value in
the Properties palette).

Everything is best-effort: an unbound parameter simply makes stamping a
no-op, it never fails a modelling run. IronPython 2.7 / Revit
2021-2026 safe.
"""

import clr
clr.AddReference("RevitAPI")

import os
import tempfile

from Autodesk.Revit.DB import (
    Transaction, BuiltInCategory, FilteredElementCollector,
    FamilyInstance,
)
from Autodesk.Revit.DB.Plumbing import Pipe

from pymep_revit import safe_name

PARAM_NAME = "pyMEP_Network"
# Fixed forever - the shared-parameter GUID every project binds.
PARAM_GUID = "b7e6f3a1-52c4-4d9e-8a37-2f1c0d9e6b58"

_CATS = ["OST_PipeCurves", "OST_PipeFitting", "OST_PipeAccessory",
         "OST_GenericModel", "OST_PlumbingFixtures",
         "OST_MechanicalEquipment"]


def _find_binding(doc):
    """(definition, binding) already in the project for PARAM_NAME."""
    try:
        it = doc.ParameterBindings.ForwardIterator()
        it.Reset()
        while it.MoveNext():
            try:
                if it.Key.Name == PARAM_NAME:
                    return it.Key, it.Current
            except Exception:
                continue
    except Exception:
        pass
    return None, None


def _category_set(doc):
    app = doc.Application
    cats = app.Create.NewCategorySet()
    for name in _CATS:
        try:
            bic = getattr(BuiltInCategory, name)
            cat = doc.Settings.Categories.get_Item(bic)
            if cat is not None:
                cats.Insert(cat)
        except Exception:
            continue
    return cats


def _external_definition(doc):
    """The shared-parameter definition, from a throwaway shared-param
    file carrying our fixed GUID. Restores the user's shared-parameter
    file path afterwards."""
    from Autodesk.Revit.DB import ExternalDefinitionCreationOptions
    from System import Guid
    app = doc.Application
    prev = None
    try:
        prev = app.SharedParametersFilename
    except Exception:
        pass
    fd, path = tempfile.mkstemp(suffix=".txt", prefix="pymep_shared_")
    os.close(fd)
    try:
        app.SharedParametersFilename = path
        spf = app.OpenSharedParameterFile()
        if spf is None:
            return None
        group = None
        try:
            group = spf.Groups.get_Item("pyMEP")
        except Exception:
            group = None
        if group is None:
            group = spf.Groups.Create("pyMEP")
        try:
            from Autodesk.Revit.DB import SpecTypeId
            opts = ExternalDefinitionCreationOptions(
                PARAM_NAME, SpecTypeId.String.Text)
        except Exception:
            from Autodesk.Revit.DB import ParameterType
            opts = ExternalDefinitionCreationOptions(
                PARAM_NAME, ParameterType.Text)
        opts.GUID = Guid(PARAM_GUID)
        return group.Definitions.Create(opts)
    finally:
        try:
            if prev is not None:
                app.SharedParametersFilename = prev
        except Exception:
            pass
        try:
            os.remove(path)
        except Exception:
            pass


def ensure_network_param(doc):
    """Bind PARAM_NAME to the pipe/fitting/node categories as an
    instance parameter under Identity Data (extending an existing
    binding with any missing category). True when the parameter is
    usable afterwards; never raises into a modelling run."""
    try:
        app = doc.Application
        have, binding = _find_binding(doc)
        want = _category_set(doc)
        if have is not None and binding is not None:
            missing = False
            try:
                for c in want:
                    if not binding.Categories.Contains(c):
                        missing = True
                        break
            except Exception:
                missing = False
            if not missing:
                return True
            try:
                for c in binding.Categories:
                    want.Insert(c)
            except Exception:
                pass
        ib = app.Create.NewInstanceBinding(want)
        t = Transaction(doc, "Bind pyMEP_Network parameter")
        t.Start()
        try:
            ok = False
            if have is not None:
                try:
                    ok = doc.ParameterBindings.ReInsert(have, ib)
                except Exception:
                    ok = False
            if not ok:
                edef = have if have is not None \
                    else _external_definition(doc)
                if edef is None:
                    t.RollBack()
                    return False
                try:
                    from Autodesk.Revit.DB import GroupTypeId
                    ok = doc.ParameterBindings.Insert(
                        edef, ib, GroupTypeId.IdentityData)
                except Exception:
                    from Autodesk.Revit.DB import BuiltInParameterGroup
                    ok = doc.ParameterBindings.Insert(
                        edef, ib, BuiltInParameterGroup.PG_IDENTITY_DATA)
            t.Commit()
            return bool(ok)
        except Exception:
            try:
                t.RollBack()
            except Exception:
                pass
            return False
    except Exception:
        return False


def stamp_network(doc, elements, network, txn=True):
    """Write ``network`` into PARAM_NAME on every element that carries
    it (None entries and paramless elements are skipped silently).
    Returns how many were stamped. ``txn=False`` when the caller already
    holds an open transaction."""
    els = [e for e in elements if e is not None]
    network = (network or "").strip()
    if not els or not network:
        return 0
    t = None
    if txn:
        t = Transaction(doc, "Stamp network name")
        t.Start()
    n = 0
    try:
        for e in els:
            try:
                p = e.LookupParameter(PARAM_NAME)
                if p is not None and not p.IsReadOnly:
                    p.Set(network)
                    n += 1
            except Exception:
                continue
        if t is not None:
            t.Commit()
    except Exception:
        if t is not None:
            try:
                t.RollBack()
            except Exception:
                pass
        return 0
    return n


def with_connected_fittings(pipes):
    """The family instances hanging off these pipes' connectors - the
    couplings/reducers/tees Revit inserted on its own, so a stamp
    covers them too."""
    out = []
    seen = set()
    for p in pipes:
        if p is None:
            continue
        try:
            conns = p.ConnectorManager.Connectors
        except Exception:
            continue
        for c in conns:
            try:
                for ref in c.AllRefs:
                    o = ref.Owner
                    if isinstance(o, FamilyInstance):
                        key = o.Id.IntegerValue
                        if key not in seen:
                            seen.add(key)
                            out.append(o)
            except Exception:
                continue
    return out


def node_network_name(node):
    """A node's network name IS its type name."""
    try:
        return safe_name(node.Symbol)
    except Exception:
        return ""


def collect_by_network(doc):
    """{network: [elements]} over every pipe / fitting / instance whose
    PARAM_NAME carries a non-empty value - the model-side network map
    the dashboard reads."""
    out = {}
    for cls in (Pipe, FamilyInstance):
        for e in FilteredElementCollector(doc).OfClass(cls):
            try:
                p = e.LookupParameter(PARAM_NAME)
                if p is None or not p.HasValue:
                    continue
                v = (p.AsString() or "").strip()
                if v:
                    out.setdefault(v, []).append(e)
            except Exception:
                continue
    return out
