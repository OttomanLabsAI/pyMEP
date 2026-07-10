# -*- coding: utf-8 -*-
# IronPython 2.7 - pyRevit
# Setup > Copy Param Value
#
# Workflow:
#   1. Pick a family TYPE from a searchable list (only its instances are used).
#   2. Pick a SOURCE parameter from a searchable list of that type's parameters.
#   3. Pick a TARGET parameter from a searchable list (writable instance params).
#   4. For every instance of that type, read the source value and write it to
#      the target. Storage types must be compatible; mismatches are reported,
#      not forced.

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


def _write_value(p, st, value):
    # Writes value into p; returns True on success. Storage types must match.
    if p.IsReadOnly:
        return False, "target is read-only"
    if p.StorageType != st:
        # Allow a couple of safe coercions.
        try:
            if p.StorageType == DB.StorageType.String:
                p.Set("" if value is None else str(value))
                return True, None
            if (p.StorageType == DB.StorageType.Double
                    and st == DB.StorageType.Integer):
                p.Set(float(value))
                return True, None
            if (p.StorageType == DB.StorageType.Integer
                    and st == DB.StorageType.Double):
                p.Set(int(round(value)))
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
    plan.append({
        "inst": fi,
        "src_param": sp,
        "tgt_param": tp,
        "st": st,
        "val": val,
        "src_disp": _param_value_repr(sp),
        "tgt_old": _param_value_repr(tp),
    })

if not plan:
    forms.alert("No instances had both parameters.", exitscript=True)

out.print_md("### Copy parameter preview")
out.print_md("**Family type:** {0}".format(_type_label(fam_choice["symbol"])))
out.print_md("**Copy:** `{0}`  ->  `{1}`  on {2} instance(s)".format(
    src_name, tgt_name, len(plan)))

rows = []
for r in plan[:200]:
    rows.append([str(r["inst"].Id.IntegerValue), r["src_disp"], r["tgt_old"]])
out.print_table(
    table_data=rows,
    columns=["Element Id", "Source value (copy)", "Target value (current)"]
)
if len(plan) > 200:
    out.print_md("_Showing first 200 of {0}._".format(len(plan)))
if skipped:
    out.print_md("**{0} instance(s) skipped** (missing a parameter).".format(
        len(skipped)))

if not forms.alert(
        "Copy '{0}' into '{1}' for {2} instance(s)?".format(
            src_name, tgt_name, len(plan)),
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
