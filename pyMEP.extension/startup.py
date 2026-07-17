# -*- coding: utf-8 -*-
"""pyMEP extension startup hook - keep the ribbon panels in order.

Revit's ribbon API cannot MOVE a panel once it exists in the running
session: a pyRevit reload rebuilds panel contents in place, and any
panel whose identity changed is recreated and APPENDED at the end of
the tab. The Setup panel's title carries the version (pyMEP v1.x.0),
so its identity changes on every release - which sent Settings /
Install Update to the far end after every update + reload.

The underlying Autodesk.Windows ribbon CAN reorder panels in-session,
so this hook re-sorts the pyMEP tab back into the layout order on the
first Idling event after every load / reload. A failure here must
never hurt Revit startup - everything is wrapped defensively.
"""

import clr

TAB_TITLE = "pyMEP"
# Same order as pyMEP.tab/bundle.yaml - matched by title prefix so the
# versioned Setup panel ("pyMEP v1.16.0") matches on plain "pyMEP".
PANEL_ORDER = ["pyMEP", "Civil 3D Conversion", "Modelling", "Topography",
               "Chamber Drawing Setup", "Parameters", "Annotate"]

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
        return True
    return False


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
