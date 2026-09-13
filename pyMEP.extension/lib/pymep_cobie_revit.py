# -*- coding: utf-8 -*-
"""COBie / Uniclass - the Revit-bound half shared by the two COBie
buttons: facts about an element, the parameter a name resolves to on
it (instance first, then type), reading and writing values, binding
missing shared parameters, loading Uniclass CSV tables and writing CSV
reports.

Revit 2018-2026: the ForgeTypeId API (SpecTypeId / GroupTypeId, 2021+)
is used when present and the older ParameterType /
BuiltInParameterGroup otherwise; ids go through pymep_revit.id_value.
"""

import datetime
import io
import os

import clr
clr.AddReference("RevitAPI")
import System

from Autodesk.Revit.DB import (
    BuiltInParameter, CategoryType, ElementId, ElementType,
    ExternalDefinitionCreationOptions, FamilyInstance,
    FilteredElementCollector, StorageType,
)

import pymep_cobie as C
from pymep_revit import id_value, safe_name

try:
    from Autodesk.Revit.DB import SpecTypeId
except ImportError:
    SpecTypeId = None
try:
    from Autodesk.Revit.DB import ParameterType
except ImportError:
    ParameterType = None
try:
    from Autodesk.Revit.DB import GroupTypeId
    _IDENTITY_GROUP = GroupTypeId.IdentityData          # Revit 2022+
except Exception:
    from Autodesk.Revit.DB import BuiltInParameterGroup
    _IDENTITY_GROUP = BuiltInParameterGroup.PG_IDENTITY_DATA

SPF_NAME = "pyMEP_shared_parameters.txt"

_SYSTEM_BIPS = ("RBS_PIPING_SYSTEM_TYPE_PARAM", "RBS_DUCT_SYSTEM_TYPE_PARAM",
                "RBS_SYSTEM_NAME_PARAM", "RBS_SYSTEM_CLASSIFICATION_PARAM")
_LEVEL_BIPS = ("FAMILY_LEVEL_PARAM", "RBS_START_LEVEL_PARAM", "LEVEL_PARAM",
               "SCHEDULE_LEVEL_PARAM", "INSTANCE_REFERENCE_LEVEL_PARAM")


def _text(v):
    return u"{0}".format(v if v is not None else u"").strip()


# ---------------------------------------------------------------------------
# what is in the document
# ---------------------------------------------------------------------------
def model_categories(doc):
    """{name: Category} of the model categories that have elements in
    the document and can carry bound parameters."""
    out = {}
    for el in FilteredElementCollector(doc).WhereElementIsNotElementType():
        try:
            cat = el.Category
            if (cat is None or cat.CategoryType != CategoryType.Model
                    or not cat.AllowsBoundParameters):
                continue
            name = _text(cat.Name)
        except Exception:
            continue
        if name and name not in out:
            out[name] = cat
    return out


def _bindings(doc):
    out = {}
    try:
        it = doc.ParameterBindings.ForwardIterator()
        it.Reset()
        while it.MoveNext():
            try:
                out[_text(it.Key.Name)] = (it.Key, it.Current)
            except Exception:
                pass
    except Exception:
        pass
    return out


def bound_parameter_names(doc):
    return set(_bindings(doc).keys())


def elements_in_scope(doc, uidoc, scope, category_names):
    """The model elements the rules run over. category_names: names to
    keep, or None for every model category."""
    if scope == C.SCOPE_SELECTION:
        pool = [doc.GetElement(i) for i in uidoc.Selection.GetElementIds()]
    elif scope == C.SCOPE_VIEW:
        pool = list(FilteredElementCollector(doc, doc.ActiveView.Id)
                    .WhereElementIsNotElementType())
    else:
        pool = list(FilteredElementCollector(doc).WhereElementIsNotElementType())
    wanted = None
    if category_names is not None:
        wanted = set(_text(c).lower() for c in category_names)
    out = []
    for el in pool:
        if el is None or isinstance(el, ElementType):
            continue
        try:
            cat = el.Category
            if cat is None or cat.CategoryType != CategoryType.Model:
                continue
            if wanted is not None and _text(cat.Name).lower() not in wanted:
                continue
        except Exception:
            continue
        out.append(el)
    return out


# ---------------------------------------------------------------------------
# facts and parameters
# ---------------------------------------------------------------------------
def _param_text(el, bip):
    try:
        p = el.get_Parameter(bip)
        if p is None or not p.HasValue:
            return u""
        s = None
        try:
            s = p.AsValueString()
        except Exception:
            s = None
        if not s:
            try:
                s = p.AsString()
            except Exception:
                s = None
        return _text(s)
    except Exception:
        return u""


def _first_text(el, bip_names):
    for n in bip_names:
        bip = getattr(BuiltInParameter, n, None)
        if bip is None:
            continue
        s = _param_text(el, bip)
        if s:
            return s
    return u""


def type_of(doc, el):
    try:
        tid = el.GetTypeId()
        if tid is None or tid == ElementId.InvalidElementId:
            return None
        return doc.GetElement(tid)
    except Exception:
        return None


def element_facts(doc, el):
    """{category, family, type, system, level, mark, id} for the rules."""
    cat = u""
    try:
        cat = _text(el.Category.Name) if el.Category is not None else u""
    except Exception:
        cat = u""
    t = type_of(doc, el)
    family = u""
    try:
        if isinstance(el, FamilyInstance):
            family = _text(safe_name(el.Symbol.Family))
        elif t is not None:
            family = _text(getattr(t, "FamilyName", u""))
    except Exception:
        family = u""
    type_name = u""
    if t is not None:
        try:
            type_name = _text(safe_name(t))
        except Exception:
            type_name = u""
    return {"category": cat, "family": family, "type": type_name,
            "system": _first_text(el, _SYSTEM_BIPS),
            "level": _first_text(el, _LEVEL_BIPS),
            "mark": _param_text(el, BuiltInParameter.ALL_MODEL_MARK),
            "id": id_value(el.Id)}


def find_param(doc, el, name):
    """(where, Parameter, holder): where is 'instance' or 'type' for a
    writable parameter, 'readonly' when it exists but cannot be set,
    None when the element has no such parameter anywhere."""
    p = None
    try:
        p = el.LookupParameter(name)
    except Exception:
        p = None
    if p is not None and not p.IsReadOnly:
        return "instance", p, el
    ro = p
    t = type_of(doc, el)
    if t is not None:
        tp = None
        try:
            tp = t.LookupParameter(name)
        except Exception:
            tp = None
        if tp is not None and not tp.IsReadOnly:
            return "type", tp, t
        if tp is not None and ro is None:
            ro = tp
    if ro is not None:
        return "readonly", ro, el
    return None, None, None


def read_param(doc, el, name):
    """The parameter a name resolves to for READING (instance first,
    then type), read-only or not; None when absent."""
    where, p, _holder = find_param(doc, el, name)
    return p


def current_text(param):
    try:
        st = param.StorageType
        if st == StorageType.String:
            return _text(param.AsString())
        if st == StorageType.Integer:
            return u"{0}".format(param.AsInteger()) if param.HasValue else u""
        if st == StorageType.Double:
            return u"{0}".format(param.AsDouble()) if param.HasValue else u""
        return _text(param.AsValueString())
    except Exception:
        return u""


def wanted_text(param, value):
    """The rule's value as it will compare with current_text: yes / no
    becomes 1 / 0 for an integer parameter."""
    try:
        st = param.StorageType
    except Exception:
        return value
    if st == StorageType.Integer:
        yn = C.to_yesno(value)
        if yn is not None:
            return u"{0}".format(yn)
        try:
            return u"{0}".format(int(float(value)))
        except Exception:
            return value
    if st == StorageType.Double:
        try:
            return u"{0}".format(float(value))
        except Exception:
            return value
    return value


def write_text(param, value):
    """(ok, detail) - the value into the parameter by storage type."""
    try:
        st = param.StorageType
        if st == StorageType.String:
            return bool(param.Set(u"{0}".format(value))), u""
        if st == StorageType.Integer:
            yn = C.to_yesno(value)
            if yn is None:
                try:
                    yn = int(float(value))
                except Exception:
                    return False, u"'{0}' is not yes/no or a number".format(value)
            return bool(param.Set(yn)), u""
        if st == StorageType.Double:
            try:
                return bool(param.Set(float(value))), u""
            except Exception:
                return False, u"'{0}' is not a number".format(value)
        return False, u"parameter holds an element id"
    except Exception as ex:
        return False, u"{0}".format(ex)


# ---------------------------------------------------------------------------
# binding shared parameters
# ---------------------------------------------------------------------------
def _creation_options(name, ptype):
    opts = None
    if SpecTypeId is not None:
        try:
            spec = (SpecTypeId.String.Text if ptype == "text"
                    else SpecTypeId.Boolean.YesNo)
            opts = ExternalDefinitionCreationOptions(name, spec)
        except Exception:
            opts = None
    if opts is None and ParameterType is not None:
        opts = ExternalDefinitionCreationOptions(
            name, ParameterType.Text if ptype == "text" else ParameterType.YesNo)
    if opts is None:
        raise RuntimeError("no way to describe a shared parameter on this Revit")
    try:
        opts.GUID = System.Guid(C.param_guid(name))
    except Exception:
        pass
    try:
        opts.UserModifiable = True
        opts.Visible = True
    except Exception:
        pass
    return opts


def _shared_file(app):
    # pyMEP's own shared parameter file beside the settings; the user's
    # file is put back afterwards.
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    folder = os.path.join(base, "pyRevit")
    if not os.path.isdir(folder):
        os.makedirs(folder)
    path = os.path.join(folder, SPF_NAME)
    if not os.path.isfile(path):
        io.open(path, "w", encoding="utf-8").close()
    previous = None
    try:
        previous = app.SharedParametersFilename
    except Exception:
        previous = None
    app.SharedParametersFilename = path
    deffile = app.OpenSharedParameterFile()
    if deffile is None:
        raise RuntimeError("Revit could not open {0}".format(path))
    return deffile, previous


def ensure_parameters(doc, specs, categories):
    """Bind every spec {name, kind, type} that is not yet bound in the
    document to the given Category objects; extend the categories of
    one that is. Call inside an open Transaction. Returns rows
    (name, status, detail) with status created / extended / exists /
    FAILED."""
    rows = []
    app = doc.Application
    cats = app.Create.NewCategorySet()
    usable = []
    for cat in categories or []:
        try:
            if cat is not None and cat.AllowsBoundParameters:
                cats.Insert(cat)
                usable.append(cat)
        except Exception:
            pass
    if not usable:
        return [(s["name"], "FAILED", u"no bindable category to bind to")
                for s in specs]
    existing = _bindings(doc)
    deffile = None
    previous = None
    if any(s["name"] not in existing for s in specs):
        try:
            deffile, previous = _shared_file(app)
        except Exception as ex:
            for s in specs:
                if s["name"] in existing:
                    rows.append((s["name"], "exists", u"already bound"))
                else:
                    rows.append((s["name"], "FAILED",
                                 u"shared parameter file: {0}".format(ex)))
            return rows
    try:
        group = None
        if deffile is not None:
            try:
                group = deffile.Groups.get_Item(C.PARAM_GROUP)
            except Exception:
                group = None
            if group is None:
                group = deffile.Groups.Create(C.PARAM_GROUP)
        for spec in specs:
            name = spec["name"]
            if name in existing:
                definition, binding = existing[name]
                try:
                    have = binding.Categories
                    added = 0
                    for cat in usable:
                        if not have.Contains(cat):
                            have.Insert(cat)
                            added += 1
                    if added:
                        ok = doc.ParameterBindings.ReInsert(definition, binding,
                                                            _IDENTITY_GROUP)
                        rows.append((name, "extended" if ok else "FAILED",
                                     u"{0} more category(ies)".format(added)))
                    else:
                        rows.append((name, "exists", u"already bound"))
                except Exception as ex:
                    rows.append((name, "exists",
                                 u"binding not extended: {0}".format(ex)))
                continue
            try:
                ext = None
                try:
                    ext = group.Definitions.get_Item(name)
                except Exception:
                    ext = None
                if ext is None:
                    ext = group.Definitions.Create(
                        _creation_options(name, spec["type"]))
                if spec["kind"] == "instance":
                    binding = app.Create.NewInstanceBinding(cats)
                else:
                    binding = app.Create.NewTypeBinding(cats)
                ok = doc.ParameterBindings.Insert(ext, binding, _IDENTITY_GROUP)
                rows.append((name, "created" if ok else "FAILED",
                             u"{0} {1}".format(spec["kind"], spec["type"])))
            except Exception as ex:
                rows.append((name, "FAILED", u"{0}".format(ex)))
    finally:
        if previous:
            try:
                app.SharedParametersFilename = previous
            except Exception:
                pass
    return rows


# ---------------------------------------------------------------------------
# tables and reports
# ---------------------------------------------------------------------------
def load_tables(folder):
    """({code: title}, info text) from every *.csv in the folder."""
    table = {}
    per_axis = {}
    files = 0
    bad = []
    if not folder or not os.path.isdir(folder):
        return {}, u"Folder not found: {0}".format(folder or u"(blank)")
    for name in sorted(os.listdir(folder)):
        if not name.lower().endswith(".csv"):
            continue
        path = os.path.join(folder, name)
        try:
            with open(path, "rb") as f:
                entries = C.parse_table_csv(f.read())
        except Exception as ex:
            bad.append(u"{0} ({1})".format(name, ex))
            continue
        if not entries:
            bad.append(u"{0} (no Code / Title columns)".format(name))
            continue
        files += 1
        for code, title in entries:
            if code not in table:
                table[code] = title
                ax = C.table_of(code)
                per_axis[ax] = per_axis.get(ax, 0) + 1
    parts = [u"{0} file(s), {1} code(s)".format(files, len(table))]
    if per_axis:
        parts.append(u", ".join(u"{0} {1}".format(k, per_axis[k])
                                for k in sorted(per_axis)))
    if bad:
        parts.append(u"skipped: " + u"; ".join(bad))
    return table, u" - ".join(parts)


def write_report(folder, stem, header, rows):
    """A timestamped CSV in folder; the path, or None when it could not
    be written."""
    try:
        if not os.path.isdir(folder):
            os.makedirs(folder)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(folder, u"{0}_{1}.csv".format(stem, stamp))

        def cell(v):
            s = u"{0}".format(v if v is not None else u"")
            if any(ch in s for ch in u',"\n\r'):
                s = u'"' + s.replace(u'"', u'""') + u'"'
            return s
        with io.open(path, "w", encoding="utf-8", newline="") as f:
            f.write(u",".join(cell(h) for h in header) + u"\r\n")
            for row in rows:
                f.write(u",".join(cell(c) for c in row) + u"\r\n")
        return path
    except Exception:
        return None
