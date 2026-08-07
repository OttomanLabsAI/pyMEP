# -*- coding: utf-8 -*-
"""Project Data Transfer - the sectioned dialog both buttons share.

One window, two sections (view templates / filters). Each section has
a 'Select...' button that opens a grouped multi-select picker - the
template picker groups by VIEW FAMILY (floor plan, 3D, section, ...)
so a family works as a filter - plus a checkbox per section choosing
what actually goes to (or comes from) the file. The import flavour
also shows the update-or-skip choice for existing same-name items.

IronPython 2.7 / pyRevit forms.
"""

import os
import sys

from pyrevit import forms

XAML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "pymep_project_data.xaml")


class _Pick(object):
    def __init__(self, label, payload):
        self.name = label
        self.payload = payload


class ProjectDataWindow(forms.WPFWindow):
    """result is None (cancel) or:
      {"a_on": bool, "a": [payloads],   # view templates
       "b_on": bool, "b": [payloads],   # filters
       "clash": "update" | "skip"}      # import flavour only

    a_items / b_items: [(label, payload)] or, for a grouped picker,
    [(group, label, payload)] - groups become the picker's family
    dropdown. Everything starts selected."""

    def __init__(self, title, info, go_label, a_items, b_items,
                 sections_header, show_clash=False):
        forms.WPFWindow.__init__(self, XAML_PATH)
        self.result = None
        self.Title = title
        self.TxtTitle.Text = title
        self.TxtInfo.Text = info
        self.BtnGo.Content = go_label
        self.GrpSections.Header = sections_header
        if show_clash:
            try:
                from System.Windows import Visibility
                self.GrpClash.Visibility = Visibility.Visible
            except Exception:
                pass
        self._a_items = [i if len(i) == 3 else (None, i[0], i[1])
                         for i in a_items]
        self._b_items = [i if len(i) == 3 else (None, i[0], i[1])
                         for i in b_items]
        self._a_picked = list(self._a_items)     # all in, until refined
        self._b_picked = list(self._b_items)
        if not self._a_items:
            self.ChkA.IsChecked = False
            self.ChkA.IsEnabled = False
            self.BtnPickA.IsEnabled = False
        if not self._b_items:
            self.ChkB.IsChecked = False
            self.ChkB.IsEnabled = False
            self.BtnPickB.IsEnabled = False
        self._refresh()

    # ------------------------------------------------------------------
    def _refresh(self):
        def label(picked, items):
            if not items:
                return "none available"
            if len(picked) == len(items):
                return "all {} selected".format(len(items))
            return "{} of {} selected".format(len(picked), len(items))
        try:
            self.LblA.Text = label(self._a_picked, self._a_items)
            self.LblB.Text = label(self._b_picked, self._b_items)
        except Exception:
            pass

    @staticmethod
    def _run_picker(title, items):
        """items: [(group, label, payload)] - grouped when any group is
        set (the group dropdown filters by view family). Returns the
        picked [(group, label, payload)] or None on cancel."""
        grouped = any(g for g, _l, _p in items)
        if grouped:
            src = {}
            for g, l, p in items:
                src.setdefault(g or "Other", []).append(
                    _Pick(l, (g, l, p)))
            for k in src:
                src[k] = sorted(src[k], key=lambda x: x.name)
            all_key = "All ({} items)".format(len(items))
            src[all_key] = sorted(
                (_Pick(l, (g, l, p)) for g, l, p in items),
                key=lambda x: x.name)
        else:
            src = sorted((_Pick(l, (g, l, p)) for g, l, p in items),
                         key=lambda x: x.name)
        picked = forms.SelectFromList.show(
            src, title=title, multiselect=True, button_name="Keep these")
        if picked is None:
            return None
        return [p.payload for p in picked]

    # ------------------------------------------------------------------
    def on_pick_a(self, sender, args):
        got = self._run_picker("Select view templates - the dropdown "
                               "filters by view family", self._a_items)
        if got is not None:
            self._a_picked = got
            if got:
                self.ChkA.IsChecked = True
        self._refresh()

    def on_pick_b(self, sender, args):
        got = self._run_picker("Select filters", self._b_items)
        if got is not None:
            self._b_picked = got
            if got:
                self.ChkB.IsChecked = True
        self._refresh()

    def on_sections(self, sender, args):
        try:
            self.BtnPickA.IsEnabled = bool(self.ChkA.IsChecked) and \
                bool(self._a_items)
            self.BtnPickB.IsEnabled = bool(self.ChkB.IsChecked) and \
                bool(self._b_items)
        except Exception:
            pass

    def on_go(self, sender, args):
        a_on = bool(self.ChkA.IsChecked)
        b_on = bool(self.ChkB.IsChecked)
        if not a_on and not b_on:
            self.StatusText.Text = ("Tick at least one section - "
                                    "nothing is chosen to transfer.")
            return
        if a_on and not self._a_picked:
            self.StatusText.Text = ("The view templates section is "
                                    "ticked but nothing is selected - "
                                    "use its Select button.")
            return
        if b_on and not self._b_picked:
            self.StatusText.Text = ("The filters section is ticked "
                                    "but nothing is selected - use "
                                    "its Select button.")
            return
        self.result = {
            "a_on": a_on,
            "a": [p for _g, _l, p in self._a_picked] if a_on else [],
            "b_on": b_on,
            "b": [p for _g, _l, p in self._b_picked] if b_on else [],
            "clash": "skip" if self.RadSkip.IsChecked else "update",
        }
        self.Close()

    def on_cancel(self, sender, args):
        self.result = None
        self.Close()
