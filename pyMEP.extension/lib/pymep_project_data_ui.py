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


def _force_on_top(win):
    """Keep the dialog in FRONT of Revit. The load-bearing part is the
    OWNER: parenting the WPF window to Revit's main window is exactly
    what pyRevit's own dialogs do, and without it a modal window can
    open BEHIND Revit ('nothing opened'). Topmost + Activate are the
    belt on top."""
    try:
        from pyrevit import HOST_APP
        from System.Windows.Interop import WindowInteropHelper
        WindowInteropHelper(win).Owner = HOST_APP.proc_window
    except Exception:
        pass
    try:
        win.Topmost = True

        def _loaded(sender, args):
            try:
                win.Activate()
                win.Topmost = False
                win.Topmost = True     # re-assert above the output
            except Exception:
                pass
        win.Loaded += _loaded
    except Exception:
        pass


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
        _force_on_top(self)
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
    """The sectioned Project Data dialog, DATA-DRIVEN: one GroupBox
    per section (Select... button + live count + optional hint), a
    'Sections to ...' group with a tick per section, and - for the
    import flavour - the update-or-skip choice for existing items.

    sections: list of dicts
      {"key":   "templates",
       "header": "View templates",
       "items":  [(group, label, payload)],   # group None = flat list
       "hint":   "..." or None,
       "pick_title": "Select view templates..."}

    auto_link (optional) ties two sections together with an
    auto-include tick INSIDE the 'to' section's box:
      {"from": "templates", "to": "filters",
       "refs": {from_label: [to_labels]},
       "text": "Automatically include ..."}

    result is None (cancel) or:
      {"sections": {key: {"on": bool, "picked": [payloads]}},
       "auto": bool, "clash": "update" | "skip"}
    """

    def __init__(self, title, info, go_label, sections,
                 sections_header, show_clash=False, auto_link=None,
                 auto_default=True, notices=None,
                 notices_header="Cannot import cleanly right now"):
        forms.WPFWindow.__init__(self, XAML_PATH)
        self.result = None
        self.Title = title
        self.TxtTitle.Text = title
        self.TxtInfo.Text = info
        self.BtnGo.Content = go_label
        _force_on_top(self)
        self.GrpSections.Header = sections_header
        if show_clash:
            try:
                from System.Windows import Visibility
                self.GrpClash.Visibility = Visibility.Visible
            except Exception:
                pass
        self._auto_link = auto_link
        self._auto_box = None
        self._sections = []
        for s in sections:
            items = [i if len(i) == 3 else (None, i[0], i[1])
                     for i in s["items"]]
            self._sections.append({
                "key": s["key"], "header": s["header"],
                "items": items, "picked": list(items),
                "hint": s.get("hint"),
                "pick_title": s.get("pick_title") or
                "Select {}".format(s["header"].lower()),
                "count_label": None, "tick": None, "button": None})
        self._build_sections(auto_default)
        self._build_notices(notices, notices_header)
        self._merge_auto()
        self._refresh()

    # ------------------------------------------------------------------
    def _build_sections(self, auto_default):
        # real WPF enums only: IronPython refuses int -> enum here
        # ('Cannot convert numeric value 2 to TextWrapping')
        from System.Windows import (HorizontalAlignment, TextWrapping,
                                    Thickness)
        from System.Windows.Controls import (Button, CheckBox, GroupBox,
                                             StackPanel, TextBlock,
                                             Orientation)
        for sec in self._sections:
            box = GroupBox()
            box.Header = sec["header"]
            box.Margin = Thickness(0, 0, 0, 12)
            box.Padding = Thickness(10, 8, 10, 10)
            body = StackPanel()
            row = StackPanel()
            row.Orientation = Orientation.Horizontal
            btn = Button()
            btn.Content = "Select..."
            btn.MinWidth = 150
            btn.Padding = Thickness(10, 4, 10, 4)
            btn.Click += self._make_pick_handler(sec)
            lbl = TextBlock()
            lbl.Margin = Thickness(12, 4, 0, 0)
            row.Children.Add(btn)
            row.Children.Add(lbl)
            body.Children.Add(row)
            if self._auto_link and self._auto_link.get("to") == sec["key"]:
                auto = CheckBox()
                auto.Content = self._auto_link.get(
                    "text", "Automatically include referenced items")
                auto.Margin = Thickness(0, 8, 0, 0)
                auto.IsChecked = bool(auto_default)
                auto.Checked += self.on_auto
                auto.Unchecked += self.on_auto
                body.Children.Add(auto)
                self._auto_box = auto
            if sec["hint"]:
                hint = TextBlock()
                hint.Text = sec["hint"]
                hint.TextWrapping = TextWrapping.Wrap
                hint.FontSize = 11.0
                hint.Margin = Thickness(0, 4, 0, 0)
                try:
                    from System.Windows.Media import Brushes
                    hint.Foreground = Brushes.Gray
                except Exception:
                    pass
                body.Children.Add(hint)
            box.Content = body
            self.PnlSections.Children.Add(box)
            tick = CheckBox()
            tick.Content = sec["header"]
            tick.Margin = Thickness(0, 4, 0, 2)
            tick.IsChecked = bool(sec["items"])
            if not sec["items"]:
                tick.IsEnabled = False
                btn.IsEnabled = False
            self.PnlSectionTicks.Children.Add(tick)
            sec["count_label"] = lbl
            sec["tick"] = tick
            sec["button"] = btn
        every = Button()
        every.Content = "Tick EVERYTHING (all sections, all items)"
        every.Margin = Thickness(0, 10, 0, 2)
        every.Padding = Thickness(10, 4, 10, 4)
        every.HorizontalAlignment = HorizontalAlignment.Left
        every.Click += self.on_everything
        self.PnlSectionTicks.Children.Add(every)

    def on_everything(self, sender, args):
        """One click = the whole file: every section on, every item
        picked."""
        for sec in self._sections:
            sec["picked"] = list(sec["items"])
            if sec["items"]:
                sec["tick"].IsChecked = True
        self._refresh()

    def _build_notices(self, notices, header):
        if not notices:
            return
        from System.Windows import TextWrapping, Thickness
        from System.Windows.Controls import GroupBox, StackPanel, \
            TextBlock
        from System.Windows.Media import Brushes
        box = GroupBox()
        box.Header = "{} ({})".format(header, len(notices))
        box.Margin = Thickness(0, 0, 0, 12)
        box.Padding = Thickness(10, 8, 10, 10)
        body = StackPanel()
        shown = list(notices)[:15]
        for line in shown:
            tb = TextBlock()
            tb.Text = u"• {}".format(line)
            tb.TextWrapping = TextWrapping.Wrap
            tb.FontSize = 11.0
            tb.Margin = Thickness(0, 2, 0, 2)
            try:
                tb.Foreground = Brushes.Firebrick
            except Exception:
                pass
            body.Children.Add(tb)
        if len(notices) > len(shown):
            more = TextBlock()
            more.Text = "... and {} more - the full list is in the " \
                "pyMEP report.".format(len(notices) - len(shown))
            more.FontSize = 11.0
            more.Margin = Thickness(0, 4, 0, 0)
            body.Children.Add(more)
        box.Content = body
        self.PnlSections.Children.Add(box)

    def _make_pick_handler(self, sec):
        def handler(sender, args):
            ordered = sorted(sec["items"],
                             key=lambda i: ((i[0] or ""), i[1]))
            win = PickListWindow(
                sec["pick_title"], ordered,
                checked_labels=[l for _g, l, _p in sec["picked"]])
            win.ShowDialog()
            if win.result is not None:
                sec["picked"] = win.result
                if win.result:
                    sec["tick"].IsChecked = True
                if self._auto_link and \
                        self._auto_link.get("from") == sec["key"]:
                    self._merge_auto()
            self._refresh()
        return handler

    def _section(self, key):
        for sec in self._sections:
            if sec["key"] == key:
                return sec
        return None

    # ------------------------------------------------------------------
    def _merge_auto(self):
        """Auto tick ON: every 'to' item the picked 'from' items
        reference joins the 'to' pick - additive only."""
        link = self._auto_link
        if not link or self._auto_box is None or \
                not bool(self._auto_box.IsChecked):
            return
        src = self._section(link.get("from"))
        dst = self._section(link.get("to"))
        if src is None or dst is None:
            return
        refs = link.get("refs") or {}
        needed = set()
        for _g, label, _p in src["picked"]:
            for name in refs.get(label) or []:
                needed.add(name)
        if not needed:
            return
        have = set(l for _g, l, _p in dst["picked"])
        added = False
        for item in dst["items"]:
            if item[1] in needed and item[1] not in have:
                dst["picked"].append(item)
                added = True
        if added and dst["picked"]:
            dst["tick"].IsChecked = True

    def on_auto(self, sender, args):
        self._merge_auto()
        self._refresh()

    # ------------------------------------------------------------------
    def _refresh(self):
        for sec in self._sections:
            try:
                n, m = len(sec["picked"]), len(sec["items"])
                if not m:
                    sec["count_label"].Text = "none available"
                elif n == m:
                    sec["count_label"].Text = \
                        "all {} selected".format(m)
                else:
                    sec["count_label"].Text = \
                        "{} of {} selected".format(n, m)
            except Exception:
                pass

    def on_go(self, sender, args):
        out = {}
        any_on = False
        for sec in self._sections:
            on = bool(sec["tick"].IsChecked)
            if on and not sec["picked"]:
                self.StatusText.Text = (
                    "The {} section is ticked but nothing is selected "
                    "- use its Select button.".format(sec["header"]))
                return
            any_on = any_on or (on and bool(sec["picked"]))
            out[sec["key"]] = {
                "on": on,
                "picked": [p for _g, _l, p in sec["picked"]]
                if on else []}
        if not any_on:
            self.StatusText.Text = ("Tick at least one section - "
                                    "nothing is chosen to transfer.")
            return
        self.result = {
            "sections": out,
            "auto": bool(self._auto_box.IsChecked)
            if self._auto_box is not None else False,
            "clash": "skip" if self.RadSkip.IsChecked else "update",
        }
        self.Close()

    def on_cancel(self, sender, args):
        self.result = None
        self.Close()
