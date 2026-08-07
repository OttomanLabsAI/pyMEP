# -*- coding: utf-8 -*-
"""Ribbon panel visibility for the pyMEP tab.

The Settings dialog's 'Ribbon Panels' page stores the hidden panels
as their display-title prefixes in pyMEP_settings.json
('hidden_panels': ["Networks", ...]) - the settings file lives in
%APPDATA%\\pyRevit, OUTSIDE the extension folder, so the choice
survives update installations. apply_panel_visibility() flips the
underlying Autodesk.Windows panels live (no reload needed) and the
startup hook re-applies it on every Revit start.

The versioned Setup panel ('pyMEP v1.x.0') is never hidden - it
carries Settings itself.
"""

import clr

TAB_TITLE = "pyMEP"

# display-title prefixes of every hideable panel, ribbon order
HIDEABLE_PANELS = ["Civil 3D Conversion", "Electrical", "Drainage",
                   "Pipe Networks", "Networks", "Topography",
                   "Chamber Drawing Setup", "Parameters",
                   "Project Data Transfer", "Annotate"]


def _panel_key(title):
    """The HIDEABLE_PANELS entry a panel title matches, or None.
    Longest prefix wins so 'Pipe Networks' never matches 'Networks'."""
    best = None
    for prefix in HIDEABLE_PANELS:
        if title.startswith(prefix):
            if best is None or len(prefix) > len(best):
                best = prefix
    return best


def apply_panel_visibility(hidden_titles):
    """Show / hide the pyMEP tab's panels: every hideable panel whose
    title prefix sits in ``hidden_titles`` goes invisible, the rest
    come (back) on. Returns True when the tab was found. Never
    raises."""
    hidden = set(hidden_titles or [])
    try:
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
            for panel in tab.Panels:
                try:
                    title = panel.Source.Title or ""
                except Exception:
                    continue
                if title.startswith(TAB_TITLE):
                    continue          # the Setup panel stays
                key = _panel_key(title)
                if key is None:
                    continue
                try:
                    panel.IsVisible = key not in hidden
                except Exception:
                    pass
            return True
    except Exception:
        pass
    return False
