# -*- coding: utf-8 -*-
# IronPython 2.7 - pyRevit
# Parameters > Replicate Parameter
#
# Workflow:
#   1. Pick a family TYPE from a searchable list (only its instances are used).
#   2. Pick a SOURCE parameter from a searchable list of that type's parameters.
#   3. Pick a TARGET parameter from a searchable list (writable instance params).
#   4. UNITS: Revit stores measured values in internal units - angles in
#      RADIANS, lengths in FEET - while Properties shows project units
#      (degrees, mm). Copying a measured value into a plain number / text
#      parameter therefore asks whether to write it AS SHOWN (degrees, mm -
#      the default) or as the raw internal number; copying a plain number
#      INTO a measured parameter asks whether that number is in the shown
#      units (converted in) or already internal. Measured-to-measured of
#      the same kind copies raw, which Revit displays correctly.
#   5. For every instance of that type, read the source value and write it
#      to the target. Storage types must be compatible; mismatches are
#      reported, not forced.

from pyrevit import revit, DB, forms, script

doc = revit.doc
out = script.get_output()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _type_label(sym):
    try:
        fam = sym.Family.Name
    except Exception:
        fam = "?"
    try:
        tname = DB.Element.Name.GetValue(sym)
    except Exception:
        tname = "?"
    return "{0} : {1}".format(fam, tname)


def _param_value_repr(p):
    # A short human-readable value for preview.
    try:
        st = p.StorageType
        if st == DB.StorageType.String:
            v = p.AsString()
            return v if v is not None else ""
        if st == DB.StorageType.Double:
            # what Properties shows (degrees, mm) - the raw number is
            # radians / feet and misleads
            try:
                vs = p.AsValueString()
                if vs:
                    return vs
            except Exception:
                pass
            return "{0:.4f}".format(p.AsDouble())
        if st == DB.StorageType.Integer:
            return str(p.AsInteger())
        if st == DB.StorageType.ElementId:
            eid = p.AsElementId()
            return str(eid.IntegerValue) if eid is not None else ""
    except Exception:
        pass
    return ""


def _read_value(p):
    # Returns (storage_type, value) for copying.
    st = p.StorageType
    if st == DB.StorageType.String:
        return st, p.AsString()
    if st == DB.StorageType.Double:
        return st, p.AsDouble()
    if st == DB.StorageType.Integer:
        return st, p.AsInteger()
    if st == DB.StorageType.ElementId:
        return st, p.AsElementId()
    return st, None


def _spec_of(p):
    """A comparable handle for the parameter's data type (spec), or
    None - two measured parameters of the SAME spec copy raw."""
    d = p.Definition
    for getter in ("GetDataType", "GetSpecTypeId"):      # 2022+ / 2021
        try:
            fn = getattr(d, getter, None)
            if fn is not None:
                sp = fn()
                if sp is not None and sp.TypeId:
                    return ("forge", sp.TypeId)
        except Exception:
            pass
    try:                                                 # <= 2020
        return ("legacy", str(d.ParameterType))
    except Exception:
        return None


def _display_unit(p):
    """The unit Properties SHOWS this parameter in (degrees, mm, ...),
    or None when it is unitless - a plain number, text or integer."""
    try:
        if p.StorageType != DB.StorageType.Double:
            return None
    except Exception:
        return None
    try:                                    # Revit 2021+
        u = p.GetUnitTypeId()
        if u is not None and u.TypeId:
            return u
    except Exception:
        pass
    try:                                    # Revit <= 2020
        dut = p.DisplayUnitType
        if str(dut) not in ("DUT_UNDEFINED", "DUT_GENERAL",
                            "DUT_CUSTOM", "DUT_FIXED"):
            return dut
    except Exception:
        pass
    return None


def _unit_label(unit):
    try:
        return DB.LabelUtils.GetLabelForUnit(unit)      # ForgeTypeId
    except Exception:
        pass
    try:
        return DB.LabelUtils.GetLabelFor(unit)          # DisplayUnitType
    except Exception:
        return "project units"


def _to_display(value, unit):
    return DB.UnitUtils.ConvertFromInternalUnits(float(value), unit)


def _to_internal(value, unit):
    return DB.UnitUtils.ConvertToInternalUnits(float(value), unit)


def _num_text(v):
    """A float as tidy text for a TEXT target: 45 not 45.000000,
    22.5 not 22.499999999."""
    try:
        txt = "{0:.6f}".format(float(v)).rstrip("0").rstrip(".")
        return txt if txt not in ("", "-", "-0") else "0"
    except Exception:
        return str(v)


def _write_value(p, st, value):
    # Writes value into p; returns True on success. Storage types must match.
    if p.IsReadOnly:
        return False, "target is read-only"
    if p.StorageType != st:
        # Allow a couple of safe coercions.
        try:
            if p.StorageType == DB.StorageType.String:
                if value is None:
                    p.Set("")
                elif isinstance(value, float):
                    p.Set(_num_text(value))
                else:
                    p.Set(str(value))
                return True, None
            if (p.StorageType == DB.StorageType.Double
                    and st in (DB.StorageType.Integer,
                               DB.StorageType.String)):
                p.Set(float(value))
                return True, None
            if (p.StorageType == DB.StorageType.Integer
                    and st in (DB.StorageType.Double,
                               DB.StorageType.String)):
                p.Set(int(round(float(value))))
                return True, None
        except Exception as ex:
            return False, "type mismatch ({0})".format(ex)
        return False, "type mismatch (source {0} -> target {1})".format(
            st, p.StorageType)
    try:
        if value is None and st == DB.StorageType.String:
            p.Set("")
        else:
            p.Set(value)
        return True, None
    except Exception as ex:
        return False, str(ex)


def _instance_params(inst):
    # Returns a dict {param_name: Parameter} for an instance's parameters.
    out_d = {}
    for p in inst.Parameters:
        try:
            nm = p.Definition.Name
        except Exception:
            continue
        if nm and nm not in out_d:
            out_d[nm] = p
    return out_d


# ---------------------------------------------------------------------------
# 1) Pick the family TYPE (searchable)
# ---------------------------------------------------------------------------
inst_collector = DB.FilteredElementCollector(doc)\
    .OfClass(DB.FamilyInstance)\
    .WhereElementIsNotElementType()\
    .ToElements()

inst_by_typeid = {}
sym_by_typeid = {}
for fi in inst_collector:
    tid = fi.GetTypeId()
    if tid is None or tid == DB.ElementId.InvalidElementId:
        continue
    key = tid.IntegerValue
    inst_by_typeid.setdefault(key, [])
    inst_by_typeid[key].append(fi)
    if key not in sym_by_typeid:
        sym_by_typeid[key] = doc.GetElement(tid)

if not inst_by_typeid:
    forms.alert("No placed family instances found in this model.",
                exitscript=True)

type_options = []
for key, insts in inst_by_typeid.items():
    sym = sym_by_typeid.get(key)
    if sym is None:
        continue
    type_options.append({
        "label": "{0}   ({1} placed)".format(_type_label(sym), len(insts)),
        "typeid": key,
        "symbol": sym,
    })
type_options.sort(key=lambda d: d["label"].lower())

picked_fam = forms.SelectFromList.show(
    [d["label"] for d in type_options],
    title="Select family TYPE (type to search)",
    button_name="Use this family type",
    multiselect=False
)
if not picked_fam:
    script.exit()

fam_choice = None
for d in type_options:
    if d["label"] == picked_fam:
        fam_choice = d
        break

instances = inst_by_typeid[fam_choice["typeid"]]


# ---------------------------------------------------------------------------
# 2) Pick the SOURCE parameter (searchable)
# ---------------------------------------------------------------------------
# Use the first instance to enumerate available parameter names.
sample = instances[0]
sample_params = _instance_params(sample)
if not sample_params:
    forms.alert("Selected family type exposes no readable instance parameters.",
                exitscript=True)

src_labels = []
for nm in sorted(sample_params.keys(), key=lambda s: s.lower()):
    p = sample_params[nm]
    ro = " [read-only]" if p.IsReadOnly else ""
    src_labels.append("{0}   = {1}{2}".format(
        nm, _param_value_repr(p), ro))

label_to_name = {}
for nm in sample_params.keys():
    p = sample_params[nm]
    ro = " [read-only]" if p.IsReadOnly else ""
    label_to_name["{0}   = {1}{2}".format(nm, _param_value_repr(p), ro)] = nm

picked_src = forms.SelectFromList.show(
    src_labels,
    title="Select SOURCE parameter to copy FROM (type to search)",
    button_name="Use as source",
    multiselect=False
)
if not picked_src:
    script.exit()
src_name = label_to_name[picked_src]


# ---------------------------------------------------------------------------
# 3) Pick the TARGET parameter (searchable, writable only)
# ---------------------------------------------------------------------------
tgt_labels = []
tgt_label_to_name = {}
for nm in sorted(sample_params.keys(), key=lambda s: s.lower()):
    if nm == src_name:
        continue
    p = sample_params[nm]
    if p.IsReadOnly:
        continue
    lbl = "{0}   (now: {1})".format(nm, _param_value_repr(p))
    tgt_labels.append(lbl)
    tgt_label_to_name[lbl] = nm

if not tgt_labels:
    forms.alert("No writable target parameters available on this family type.",
                exitscript=True)

picked_tgt = forms.SelectFromList.show(
    tgt_labels,
    title="Select TARGET parameter to write TO (type to search)",
    button_name="Use as target",
    multiselect=False
)
if not picked_tgt:
    script.exit()
tgt_name = tgt_label_to_name[picked_tgt]


# ---------------------------------------------------------------------------
# 3b) UNITS - degrees or radians, mm or feet?
# ---------------------------------------------------------------------------
# Revit keeps measured values in INTERNAL units (angles in radians,
# lengths in feet); Properties shows PROJECT units (degrees, mm). A
# measured value copied into a plain number / text parameter would
# land as radians unless converted, so ask - as shown is the default.
_src_p = sample_params[src_name]
_tgt_p = sample_params[tgt_name]
src_unit = _display_unit(_src_p)
tgt_unit = _display_unit(_tgt_p)
_src_spec, _tgt_spec = _spec_of(_src_p), _spec_of(_tgt_p)
same_measure = (src_unit is not None and tgt_unit is not None and
                _src_spec is not None and _src_spec == _tgt_spec)

unit_mode = "raw"           # raw | display | to_internal
unit_note = ""
if src_unit is not None and not same_measure:
    lbl = _unit_label(src_unit)
    kind = {DB.StorageType.String: "TEXT",
            DB.StorageType.Integer: "whole-number",
            DB.StorageType.Double: "plain number"}.get(
                _tgt_p.StorageType, "plain")
    choice = forms.alert(
        "'{0}' is a MEASURED value - Properties shows it in {1}, but "
        "Revit stores it internally in other units (angles in radians, "
        "lengths in feet).\n\n'{2}' is a {3} parameter. Which number "
        "should it receive?".format(src_name, lbl, tgt_name, kind),
        title="Units",
        options=["As shown ({0})".format(lbl),
                 "Raw internal value", "Cancel"])
    if not choice or choice == "Cancel":
        script.exit()
    if choice.startswith("As shown"):
        unit_mode = "display"
        unit_note = "converted to {0} (as shown in Properties)".format(lbl)
    else:
        unit_note = "raw internal value (radians / feet)"
elif tgt_unit is not None and src_unit is None and \
        _src_p.StorageType in (DB.StorageType.Double,
                               DB.StorageType.Integer,
                               DB.StorageType.String):
    lbl = _unit_label(tgt_unit)
    choice = forms.alert(
        "'{0}' is a MEASURED parameter shown in {1}; '{2}' is a plain "
        "value.\n\nIs that plain value written in {1} (it will be "
        "converted into Revit's internal units), or is it already an "
        "internal value (radians / feet)?".format(tgt_name, lbl,
                                                  src_name),
        title="Units",
        options=["It is in {0}".format(lbl),
                 "Already internal", "Cancel"])
    if not choice or choice == "Cancel":
        script.exit()
    if choice.startswith("It is in"):
        unit_mode = "to_internal"
        unit_note = "read as {0}, converted to internal units".format(lbl)
    else:
        unit_note = "taken as an internal value"
elif same_measure:
    unit_note = "same kind of measure - copied as is"


def _convert(st, val):
    """(storage type to write as, value) after the units decision."""
    if val is None:
        return st, val
    try:
        if unit_mode == "display":
            return DB.StorageType.Double, _to_display(val, src_unit)
        if unit_mode == "to_internal":
            return DB.StorageType.Double, _to_internal(val, tgt_unit)
    except Exception:
        return st, val
    return st, val


def _write_disp(st, val):
    if val is None:
        return ""
    if st == DB.StorageType.Double:
        return _num_text(val)
    return str(val)


# ---------------------------------------------------------------------------
# 4) Build plan + preview
# ---------------------------------------------------------------------------
plan = []
skipped = []
for fi in instances:
    params = _instance_params(fi)
    sp = params.get(src_name)
    tp = params.get(tgt_name)
    if sp is None or tp is None:
        skipped.append((fi.Id.IntegerValue, "missing source or target param"))
        continue
    st, val = _read_value(sp)
    wst, wval = _convert(st, val)
    plan.append({
        "inst": fi,
        "src_param": sp,
        "tgt_param": tp,
        "st": wst,
        "val": wval,
        "src_disp": _param_value_repr(sp),
        "write_disp": _write_disp(wst, wval),
        "tgt_old": _param_value_repr(tp),
    })

if not plan:
    forms.alert("No instances had both parameters.", exitscript=True)

out.print_md("### Copy parameter preview")
out.print_md("**Family type:** {0}".format(_type_label(fam_choice["symbol"])))
out.print_md("**Copy:** `{0}`  ->  `{1}`  on {2} instance(s)".format(
    src_name, tgt_name, len(plan)))
if unit_note:
    out.print_md("**Units:** {0}".format(unit_note))

rows = []
for r in plan[:200]:
    rows.append([str(r["inst"].Id.IntegerValue), r["src_disp"],
                 r["write_disp"], r["tgt_old"]])
out.print_table(
    table_data=rows,
    columns=["Element Id", "Source (as shown)", "Value to write",
             "Target value (current)"]
)
if len(plan) > 200:
    out.print_md("_Showing first 200 of {0}._".format(len(plan)))
if skipped:
    out.print_md("**{0} instance(s) skipped** (missing a parameter).".format(
        len(skipped)))

if not forms.alert(
        "Copy '{0}' into '{1}' for {2} instance(s)?{3}".format(
            src_name, tgt_name, len(plan),
            "\n\nUnits: {0}.".format(unit_note) if unit_note else ""),
        yes=True, no=True):
    script.exit()


# ---------------------------------------------------------------------------
# 5) Apply
# ---------------------------------------------------------------------------
written = 0
errors = []
t = DB.Transaction(doc, "Copy parameter value")
t.Start()
try:
    for r in plan:
        ok, err = _write_value(r["tgt_param"], r["st"], r["val"])
        if ok:
            written += 1
        else:
            errors.append("Id {0}: {1}".format(r["inst"].Id.IntegerValue, err))
    t.Commit()
except Exception as ex:
    t.RollBack()
    forms.alert("Transaction failed, no changes made:\n{0}".format(ex),
                exitscript=True)

out.print_md("**Done. Wrote {0} of {1} instances.**".format(written, len(plan)))
if errors:
    out.print_md("**{0} errors:**".format(len(errors)))
    for e in errors[:50]:
        out.print_md("- " + e)
    if len(errors) > 50:
        out.print_md("_...and {0} more._".format(len(errors) - 50))
