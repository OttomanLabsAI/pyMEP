# -*- coding: utf-8 -*-
"""View template / filter transfer - the Revit API compatibility shim.

One place for everything that drifted across Revit 2022-2026, so the
serialize / deserialize modules stay version-blind:

  - ElementId.Value (2024+) vs .IntegerValue
  - BuiltInCategory / BuiltInParameter name <-> id maps (via
    System.Enum, no hand-kept tables)
  - ParameterFilterRuleFactory string rules: <=2022 took a trailing
    case-sensitivity bool, 2023+ dropped it - both signatures tried
  - fill / line pattern lookups by name, with the built-in Solid
    specials

hasattr checks everywhere; never sniff the version number.
"""

import clr
clr.AddReference("RevitAPI")

import System

from Autodesk.Revit.DB import (
    BuiltInCategory, BuiltInParameter, ElementId,
    FilteredElementCollector, FillPatternElement, LinePatternElement,
    ParameterFilterRuleFactory,
)

SOLID_LINE = "Solid"          # built-in line pattern (no element)
SOLID_FILL = "<Solid fill>"   # the solid fill pattern's display name


def id_value(eid):
    try:
        return eid.Value              # Revit 2024+
    except AttributeError:
        return eid.IntegerValue       # Revit 2023 and earlier


def make_id(value):
    try:
        return ElementId(System.Int64(value))   # 2024+ long ids
    except Exception:
        return ElementId(int(value))


def revit_version(doc):
    try:
        app = doc.Application
        return app.VersionNumber, app.VersionBuild
    except Exception:
        return "", ""


# ---------------------------------------------------------------------------
# enum name <-> id maps, built once from the running API
# ---------------------------------------------------------------------------
_BIC_BY_NAME = None
_BIC_BY_ID = None
_BIP_BY_NAME = None
_BIP_BY_ID = None


def _enum_maps(enum_type):
    by_name = {}
    by_id = {}
    for name in System.Enum.GetNames(enum_type):
        try:
            val = int(System.Enum.Parse(enum_type, name))
        except Exception:
            continue
        by_name[name] = val
        # first name wins when values alias (keeps maps stable)
        if val not in by_id:
            by_id[val] = name
    return by_name, by_id


def bic_maps():
    global _BIC_BY_NAME, _BIC_BY_ID
    if _BIC_BY_NAME is None:
        _BIC_BY_NAME, _BIC_BY_ID = _enum_maps(BuiltInCategory)
    return _BIC_BY_NAME, _BIC_BY_ID


def bip_maps():
    global _BIP_BY_NAME, _BIP_BY_ID
    if _BIP_BY_NAME is None:
        _BIP_BY_NAME, _BIP_BY_ID = _enum_maps(BuiltInParameter)
    return _BIP_BY_NAME, _BIP_BY_ID


def bic_name(category_id):
    """BuiltInCategory enum name for a category ElementId (or int),
    None for non-builtin categories."""
    v = category_id if isinstance(category_id, int) else id_value(category_id)
    return bic_maps()[1].get(v)


def bic_id(name):
    """Category id int for a BuiltInCategory enum name, None when the
    running Revit does not know it (older release)."""
    return bic_maps()[0].get(name)


def bip_name(param_id):
    v = param_id if isinstance(param_id, int) else id_value(param_id)
    return bip_maps()[1].get(v)


def bip_id(name):
    return bip_maps()[0].get(name)


# ---------------------------------------------------------------------------
# filter rule construction (string rules changed signature in 2023)
# ---------------------------------------------------------------------------
_STRING_FACTORY = {
    "equals": "CreateEqualsRule",
    "contains": "CreateContainsRule",
    "begins": "CreateBeginsWithRule",
    "ends": "CreateEndsWithRule",
    "greater": "CreateGreaterRule",
    "greater_or_equal": "CreateGreaterOrEqualRule",
    "less": "CreateLessRule",
    "less_or_equal": "CreateLessOrEqualRule",
}
_NUMERIC_FACTORY = {
    "equals": "CreateEqualsRule",
    "greater": "CreateGreaterRule",
    "greater_or_equal": "CreateGreaterOrEqualRule",
    "less": "CreateLessRule",
    "less_or_equal": "CreateLessOrEqualRule",
}


def create_string_rule(param_id, evaluator, value):
    """A FilterStringRule for the schema evaluator word - tries the
    2023+ signature first, then the <=2022 one with the trailing
    case-sensitivity bool (case-sensitive, Revit's old default)."""
    fname = _STRING_FACTORY.get(evaluator)
    if fname is None:
        raise ValueError("unknown string evaluator '{}'".format(evaluator))
    factory = getattr(ParameterFilterRuleFactory, fname)
    try:
        return factory(param_id, value)
    except TypeError:
        return factory(param_id, value, True)


def create_double_rule(param_id, evaluator, value, epsilon):
    fname = _NUMERIC_FACTORY.get(evaluator)
    if fname is None:
        raise ValueError("unknown double evaluator '{}'".format(evaluator))
    return getattr(ParameterFilterRuleFactory, fname)(
        param_id, float(value), float(epsilon))


def create_integer_rule(param_id, evaluator, value):
    fname = _NUMERIC_FACTORY.get(evaluator)
    if fname is None:
        raise ValueError("unknown integer evaluator '{}'".format(evaluator))
    return getattr(ParameterFilterRuleFactory, fname)(
        param_id, int(value))


def create_element_id_rule(param_id, evaluator, target_id):
    fname = _NUMERIC_FACTORY.get(evaluator)
    if fname is None:
        raise ValueError("unknown id evaluator '{}'".format(evaluator))
    return getattr(ParameterFilterRuleFactory, fname)(
        param_id, target_id)


def create_has_value_rule(param_id, present):
    if present:
        return ParameterFilterRuleFactory.CreateHasValueParameterRule(
            param_id)
    return ParameterFilterRuleFactory.CreateHasNoValueParameterRule(
        param_id)


# ---------------------------------------------------------------------------
# pattern lookups
# ---------------------------------------------------------------------------
def fill_pattern_by_name(doc, name):
    """FillPatternElement id by display name (drafting or model), the
    solid fill matched by its IsSolid flag too. None when missing."""
    if not name:
        return None
    for fp in FilteredElementCollector(doc).OfClass(FillPatternElement):
        try:
            pat = fp.GetFillPattern()
            if fp.Name == name:
                return fp.Id
            if name == SOLID_FILL and pat.IsSolidFill:
                return fp.Id
        except Exception:
            continue
    return None


def fill_pattern_name(doc, pattern_id):
    if pattern_id is None or id_value(pattern_id) < 0:
        return None
    el = doc.GetElement(pattern_id)
    if el is None:
        return None
    try:
        if el.GetFillPattern().IsSolidFill:
            return SOLID_FILL
    except Exception:
        pass
    try:
        return el.Name
    except Exception:
        return None


def line_pattern_by_name(doc, name):
    """LinePatternElement id by name; the built-in Solid pattern comes
    from its static id, not an element. None when missing."""
    if not name:
        return None
    if name == SOLID_LINE:
        try:
            return LinePatternElement.GetSolidPatternId()
        except Exception:
            pass
    for lp in FilteredElementCollector(doc).OfClass(LinePatternElement):
        try:
            if lp.Name == name:
                return lp.Id
        except Exception:
            continue
    return None


def line_pattern_name(doc, pattern_id):
    if pattern_id is None or id_value(pattern_id) < 0:
        try:
            solid = LinePatternElement.GetSolidPatternId()
            if pattern_id is not None and \
                    id_value(pattern_id) == id_value(solid):
                return SOLID_LINE
        except Exception:
            pass
        return None
    try:
        solid = LinePatternElement.GetSolidPatternId()
        if id_value(pattern_id) == id_value(solid):
            return SOLID_LINE
    except Exception:
        pass
    el = doc.GetElement(pattern_id)
    if el is None:
        return None
    try:
        return el.Name
    except Exception:
        return None
