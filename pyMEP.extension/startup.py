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
               "Networks", "Topography", "Chamber Drawing Setup",
               "Parameters", "Annotate"]

# Stacked buttons shown with no label (their tooltips still carry the
# names): big icons in a two-high stack, standard size in a three-high
# one. Titles normalized to single-space before matching.
ICON_ONLY = set(["Create Pipe Sizes", "Structure to Pipe",
                 "Apply Edits", "Network Settings"])

# Standalone buttons shown SMALL with no label: a slim column of their
# own, standard icon size - not a full-height large button.
ICON_ONLY_SMALL = set(["Family at Pipe Top"])

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
        return True
    return False


def _leaf_count(items):
    """How many actual buttons live in this container (row breaks and
    nested panels don't count)."""
    n = 0
    for it in items:
        if getattr(it, "Items", None) is not None:
            continue
        if hasattr(it, "Size"):
            n += 1
    return n


def _enlarge_stacked(items, siblings=0):
    """Recursively find the ICON_ONLY buttons (stacks live inside row
    panels) and drop their labels. Two-high stacks get BIG icons; a
    three-high stack keeps the standard size (three large icons do not
    fit the ribbon row height - the stack would be clipped)."""
    from Autodesk.Windows import RibbonItemSize
    for item in items:
        sub = getattr(item, "Items", None)
        if sub is not None:
            _enlarge_stacked(sub, siblings=_leaf_count(sub))
            continue
        try:
            text = " ".join(str(item.Text or "").split())
        except Exception:
            continue
        if text not in ICON_ONLY and text not in ICON_ONLY_SMALL:
            continue
        try:
            if item.LargeImage is None and item.Image is not None:
                item.LargeImage = item.Image
            if item.Image is None and item.LargeImage is not None:
                item.Image = item.LargeImage
            if text in ICON_ONLY_SMALL or siblings >= 3:
                item.Size = RibbonItemSize.Standard
            else:
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
