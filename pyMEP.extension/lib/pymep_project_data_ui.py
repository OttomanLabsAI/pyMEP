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
PICK_XAML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "pymep_pick_list.xaml")


class PickListWindow(forms.WPFWindow):
    """Grouped multi-select picker whose tick state lives OUTSIDE the
    list: switching the group filter or typing in the search box only
    changes what is VISIBLE - ticks made in any group survive until
    OK / Cancel. items: [(group, label, payload)]; checked_labels
    seeds the ticks. result: picked [(group, label, payload)] in the
    input order, or None on cancel."""

    ALL = "(all groups)"

    def __init__(self, title, items, checked_labels=None):
        forms.WPFWindow.__init__(self, PICK_XAML_PATH)
        self.result = None
        self.Title = title
        self.TxtTitle.Text = title
        self._items = list(items)
        checked = set(checked_labels if checked_labels is not None
                      else [l for _g, l, _p in self._items])
        self._state = [l in checked for _g, l, _p in self._items]
        self._boxes = []          # (checkbox, index) currently visible
        groups = sorted(set(g for g, _l, _p in self._items if g))
        self.CmbGroup.Items.Clear()
        self.CmbGroup.Items.Add("{} ({})".format(self.ALL,
                                                 len(self._items)))
        for g in groups:
            n = sum(1 for gg, _l, _p in self._items if gg == g)
            self.CmbGroup.Items.Add("{} ({})".format(g, n))
        self.CmbGroup.SelectedIndex = 0
        try:
            from System.Windows import Visibility
            if not groups:
                self.CmbGroup.Visibility = Visibility.Collapsed
        except Exception:
            pass
        self._rebuild()

    # ------------------------------------------------------------------
    def _current_group(self):
        idx = self.CmbGroup.SelectedIndex
        if idx <= 0:
            return None
        label = str(self.CmbGroup.Items[idx])
        return label.rsplit(" (", 1)[0]

    def _visible_indexes(self):
        group = self._current_group()
        needle = ""
        try:
            needle = (self.TxtSearch.Text or "").strip().lower()
        except Exception:
            pass
        out = []
        for i, (g, label, _p) in enumerate(self._items):
            if group is not None and g != group:
                continue
            if needle and needle not in label.lower():
                continue
            out.append(i)
        return out

    def _rebuild(self):
        try:
            from System.Windows.Controls import CheckBox
        except Exception:
            return
        self.PnlItems.Children.Clear()
        self._boxes = []
        for i in self._visible_indexes():
            _g, label, _p = self._items[i]
            cb = CheckBox()
            cb.Content = label
            cb.IsChecked = self._state[i]
            cb.Margin = self._box_margin()
            cb.Checked += self._on_box
            cb.Unchecked += self._on_box
            cb.Tag = i
            self.PnlItems.Children.Add(cb)
            self._boxes.append((cb, i))
        self._refresh_count()

    @staticmethod
    def _box_margin():
        from System.Windows import Thickness
        return Thickness(0, 3, 0, 3)

    def _on_box(self, sender, args):
        try:
            self._state[int(sender.Tag)] = bool(sender.IsChecked)
        except Exception:
            pass
        self._refresh_count()

    def _refresh_count(self):
        try:
            self.TxtCount.Text = "{} of {} ticked".format(
                sum(1 for s in self._state if s), len(self._state))
        except Exception:
            pass

    # ------------------------------------------------------------------
    def on_group(self, sender, args):
        self._rebuild()

    def on_search(self, sender, args):
        self._rebuild()

    def on_all(self, sender, args):
        for cb, i in self._boxes:
            cb.IsChecked = True          # handler updates state

    def on_none(self, sender, args):
        for cb, i in self._boxes:
            cb.IsChecked = False

    def on_ok(self, sender, args):
        self.result = [self._items[i]
                       for i in range(len(self._items))
                       if self._state[i]]
        self.Close()

    def on_cancel(self, sender, args):
        self.result = None
        self.Close()


class ProjectDataWindow(forms.WPFWindow):
    """result is None (cancel) or:
      {"a_on": bool, "a": [payloads],   # view templates
       "b_on": bool, "b": [payloads],   # filters
       "clash": "update" | "skip"}      # import flavour only

    a_items / b_items: [(label, payload)] or, for a grouped picker,
    [(group, label, payload)] - groups become the picker's family
    dropdown. Everything starts selected."""

    def __init__(self, title, info, go_label, a_items, b_items,
                 sections_header, show_clash=False, a_refs=None,
                 auto_default=True):
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
        # a_refs: {a-label: [b-labels]} - which filters each template
        # USES; drives the auto-include tick. None hides the tick.
        self._a_refs = a_refs
        if a_refs is None:
            try:
                from System.Windows import Visibility
                self.ChkAutoFilters.Visibility = Visibility.Collapsed
            except Exception:
                pass
        else:
            self.ChkAutoFilters.IsChecked = bool(auto_default)
        if not self._a_items:
            self.ChkA.IsChecked = False
            self.ChkA.IsEnabled = False
            self.BtnPickA.IsEnabled = False
        if not self._b_items:
            self.ChkB.IsChecked = False
            self.ChkB.IsEnabled = False
            self.BtnPickB.IsEnabled = False
        self._merge_auto()
        self._refresh()

    # ------------------------------------------------------------------
    def _merge_auto(self):
        """With the auto tick ON, every filter the picked templates
        USE joins the filters pick (never removes anything the user
        ticked themselves)."""
        try:
            on = bool(self.ChkAutoFilters.IsChecked)
        except Exception:
            on = False
        if not on or not self._a_refs:
            return
        needed = set()
        for _g, label, _p in self._a_picked:
            for fname in self._a_refs.get(label) or []:
                needed.add(fname)
        if not needed:
            return
        have = set(l for _g, l, _p in self._b_picked)
        added = False
        for item in self._b_items:
            if item[1] in needed and item[1] not in have:
                self._b_picked.append(item)
                added = True
        if added and self._b_picked:
            self.ChkB.IsChecked = True

    def on_auto(self, sender, args):
        self._merge_auto()
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
    def _run_picker(title, items, picked_now):
        """items: [(group, label, payload)] - the group dropdown
        filters by view family and ticks SURVIVE group / search
        changes. Opens with the current pick ticked. Returns the new
        picked list or None on cancel."""
        ordered = sorted(items, key=lambda i: ((i[0] or ""), i[1]))
        win = PickListWindow(
            title, ordered,
            checked_labels=[l for _g, l, _p in picked_now])
        win.ShowDialog()
        return win.result

    # ------------------------------------------------------------------
    def on_pick_a(self, sender, args):
        got = self._run_picker("Select view templates - the dropdown "
                               "filters by view family", self._a_items,
                               self._a_picked)
        if got is not None:
            self._a_picked = got
            if got:
                self.ChkA.IsChecked = True
            self._merge_auto()
        self._refresh()

    def on_pick_b(self, sender, args):
        got = self._run_picker("Select filters", self._b_items,
                               self._b_picked)
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
        try:
            auto = bool(self.ChkAutoFilters.IsChecked)
        except Exception:
            auto = False
        self.result = {
            "a_on": a_on,
            "a": [p for _g, _l, p in self._a_picked] if a_on else [],
            "b_on": b_on,
            "b": [p for _g, _l, p in self._b_picked] if b_on else [],
            "clash": "skip" if self.RadSkip.IsChecked else "update",
            "auto": auto,
        }
        self.Close()

    def on_cancel(self, sender, args):
        self.result = None
        self.Close()
