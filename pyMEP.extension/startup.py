# -*- coding: utf-8 -*-
"""pyMEP extension startup hook - keep the ribbon panels in order and
dress the stacked buttons.

Revit's ribbon API cannot MOVE a panel once it exists in the running
session: a pyRevit reload rebuilds panel contents in place, and any
panel whose identity changed is recreated and APPENDED at the end of
the tab. The Setup panel's title carries the version (pyMEP v1.x.0),
so its identity changes on every release - which sent Settings /
Install Update to the far end after every update + reload.

The underlying Autodesk.Windows ribbon CAN reorder panels in-session,
so this hook re-sorts the pyMEP tab back into the layout order on the
first Idling event after every load / reload. The same pass turns the
named stacked buttons into BIG ICON-ONLY buttons (32 px, no label) -
stacks are limited to small text buttons through the official API, but
the underlying ribbon takes Size/ShowText happily. A failure here must
never hurt Revit startup - everything is wrapped defensively.
"""

import clr

TAB_TITLE = "pyMEP"
# Same order as pyMEP.tab/bundle.yaml - matched by title prefix so the
# versioned Setup panel ("pyMEP v1.16.0") matches on plain "pyMEP".
PANEL_ORDER = ["pyMEP", "Civil 3D Conversion", "Electrical", "Drainage",
               "Pipe Networks", "Networks", "Topography",
               "Chamber Drawing Setup", "Parameters",
               "Project Data Transfer", "Annotate"]

# Stacked buttons shown with no label (their tooltips still carry the
# names): big icons in a two-high stack, standard size in a three-high
# one. Titles normalized to single-space before matching.
# Buttons shown as a BIG icon with no label (like the Networks stack) -
# the name lives in the tooltip. Works in two-high stacks and
# standalone slots alike.
ICON_ONLY = set(["Apply Edits", "Network Settings", "Create Pipe Sizes",
                 "Structure to Pipe", "Family at Pipe Top"])

# A THREE-high stack is left completely alone - restyling its items
# re-flows the row and pushes the last button off the panel. (No pyMEP
# stack is three high; this is a guard, not a layout choice.)
UNTOUCHED_STACK_SIZE = 3

_state = {"tries": 0}


def _reorder_pymep_panels():
    """Sort the pyMEP tab's panels to PANEL_ORDER. True when the tab
    was found and processed."""
    clr.AddReference("AdWindows")
    from Autodesk.Windows import ComponentManager
    ribbon = ComponentManager.Ribbon
    if ribbon is None:
        return False
    for tab in ribbon.Tabs:
        try:
            if tab.Title != TAB_TITLE:
                continue
        except Exception:
            continue
        panels = tab.Panels

        def rank(panel):
            try:
                title = panel.Source.Title or ""
            except Exception:
                title = ""
            for i, prefix in enumerate(PANEL_ORDER):
                if title.startswith(prefix):
                    return i
            return len(PANEL_ORDER)

        # Selection sort via ObservableCollection.Move - the collection
        # is live WPF state, so Move (not Remove/Insert) keeps it happy.
        n = panels.Count
        for target in range(n):
            best = target
            for j in range(target + 1, n):
                if rank(panels[j]) < rank(panels[best]):
                    best = j
            if best != target:
                panels.Move(best, target)

        for panel in panels:
            try:
                _enlarge_stacked(panel.Source.Items)
            except Exception:
                pass
        # hidden-panels choice from Settings > Ribbon Panels - stored
        # in %APPDATA%\pyRevit\pyMEP_settings.json, so it survives
        # update installations and re-applies on every start
        try:
            from pymep_config import load_settings
            from pymep_ribbon import apply_panel_visibility
            apply_panel_visibility(
                load_settings().get("hidden_panels") or [])
        except Exception:
            pass
        return True
    return False


def _button_count(items):
    """How many buttons sit in this container. Row breaks are RibbonItems
    too, so count only the things that carry a label."""
    n = 0
    for it in items:
        if getattr(it, "Items", None) is not None:
            continue
        if getattr(it, "Text", None) is not None:
            n += 1
    return n


def _enlarge_stacked(items, siblings=0):
    """Recursively find the ICON_ONLY buttons (stacks live inside row
    panels) and show them big with no label.

    A column of UNTOUCHED_STACK_SIZE or more is skipped entirely: its
    row is laid out for that many labelled standard buttons, and both
    resizing an item and hiding its text re-flow the row and push the
    last button off the panel."""
    from Autodesk.Windows import RibbonItemSize
    for item in items:
        sub = getattr(item, "Items", None)
        if sub is not None:
            _enlarge_stacked(sub, siblings=_button_count(sub))
            continue
        if siblings >= UNTOUCHED_STACK_SIZE:
            continue
        try:
            text = " ".join(str(item.Text or "").split())
        except Exception:
            continue
        if text not in ICON_ONLY:
            continue
        try:
            if item.LargeImage is None and item.Image is not None:
                item.LargeImage = item.Image
            item.Size = RibbonItemSize.Large
            item.ShowText = False
        except Exception:
            pass


def _on_idling(sender, args):
    _state["tries"] += 1
    done = False
    try:
        done = _reorder_pymep_panels()
    except Exception:
        done = False
    # Give the ribbon a few idles to finish building; then stop trying
    # either way so the handler never lingers.
    if done or _state["tries"] >= 50:
        try:
            sender.Idling -= _on_idling
        except Exception:
            pass


try:
    __revit__.Idling += _on_idling
except Exception:
    pass
