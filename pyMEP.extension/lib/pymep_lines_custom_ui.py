# -*- coding: utf-8 -*-
"""The clickable custom-slope plan window, shared by Lines to Pipes and
Update Pipes. Custom lines are orange until a gradient is applied
(green); the selected one is blue. Pure WPF - only usable inside Revit
(pyRevit forms); the fit maths it draws with lives in
pymep_lines_to_pipes.fit_plan and is unit-tested there."""

from pyrevit import forms

from pymep_lines_to_pipes import fit_plan


class CustomSlopeWindow(forms.WPFWindow):
    """A clickable plan of the whole network. Custom lines are orange
    until a gradient is applied (green); the selected one is blue.
    result = {line_index: slope_n} for every custom line, or None."""

    GREY = "#B0B0B0"
    ORANGE = "#D97B2A"
    GREEN = "#2E7D32"
    BLUE = "#1565C0"

    def __init__(self, xaml_path, lines_mm, custom_idx, preset=None):
        self._xaml_path = xaml_path
        forms.WPFWindow.__init__(self, xaml_path)
        self.result = None
        self._lines = lines_mm
        self._custom = list(custom_idx)
        self._slopes = dict(preset or {})
        self._shapes = {}
        self._selected = None
        self.TxtInfo.Text = ("{} 'Slope Custom' line(s) need a gradient. "
                             "Click an orange line, type its 1:n, Apply. "
                             "Green = done.".format(len(self._custom)))
        self._update_status()

    def _brush(self, hex_str):
        from System.Windows.Media import BrushConverter
        return BrushConverter().ConvertFromString(hex_str)

    def _redraw(self):
        from System.Windows.Shapes import Line as WpfLine
        from System.Windows import Input
        self.CnvPlan.Children.Clear()
        self._shapes = {}
        w = self.CnvPlan.ActualWidth or 560
        h = self.CnvPlan.ActualHeight or 360
        if w < 40 or h < 40:
            return
        scale, ox, oy = fit_plan(self._lines, w, h)

        def cx(p):
            return p[0] * scale + ox

        def cy(p):
            return -p[1] * scale + oy

        for i, (a, b) in enumerate(self._lines):
            ln = WpfLine()
            ln.X1, ln.Y1 = cx(a), cy(a)
            ln.X2, ln.Y2 = cx(b), cy(b)
            if i in self._custom:
                if i == self._selected:
                    ln.Stroke = self._brush(self.BLUE)
                    ln.StrokeThickness = 6.0
                elif i in self._slopes:
                    ln.Stroke = self._brush(self.GREEN)
                    ln.StrokeThickness = 4.0
                else:
                    ln.Stroke = self._brush(self.ORANGE)
                    ln.StrokeThickness = 4.0
                ln.Cursor = Input.Cursors.Hand
                ln.MouseLeftButtonDown += self._make_click(i)
            else:
                ln.Stroke = self._brush(self.GREY)
                ln.StrokeThickness = 1.5
            self.CnvPlan.Children.Add(ln)
            self._shapes[i] = ln

    def _make_click(self, index):
        def handler(sender, args):
            self._select(index)
        return handler

    def _select(self, index):
        self._selected = index
        a, b = self._lines[index]
        import math as _m
        length = _m.hypot(b[0] - a[0], b[1] - a[1]) / 1000.0
        got = self._slopes.get(index)
        self.TxtCurrent.Text = (
            "Custom line - {:.1f} m long{}".format(
                length,
                " - currently 1:{:g}".format(got) if got else ""))
        if got:
            self.TxtCustomSlope.Text = "{:g}".format(got)
        try:
            self.TxtCustomSlope.Focus()
            self.TxtCustomSlope.SelectAll()
        except Exception:
            pass
        self._redraw()

    def _next_pending(self):
        for i in self._custom:
            if i not in self._slopes:
                return i
        return None

    def _update_status(self):
        left = len([i for i in self._custom if i not in self._slopes])
        if left:
            self.StatusText.Text = ("{} line(s) still need a "
                                    "gradient.".format(left))
        else:
            self.StatusText.Text = ""

    def on_canvas_size(self, sender, args):
        self._redraw()
        if self._selected is None:
            nxt = self._next_pending()
            if nxt is not None:
                self._select(nxt)

    def on_apply(self, sender, args):
        if self._selected is None:
            self.StatusText.Text = "Click a line in the plan first."
            return
        try:
            n = float(self.TxtCustomSlope.Text)
            if n <= 0:
                raise ValueError()
        except Exception:
            self.StatusText.Text = "The gradient must be a positive 1:n."
            return
        self._slopes[self._selected] = n
        self._selected = None
        self.TxtCustomSlope.Text = ""
        nxt = self._next_pending()
        if nxt is not None:
            self._select(nxt)
        else:
            self.TxtCurrent.Text = "All custom lines have a gradient."
            self._redraw()
        self._update_status()

    def on_ok(self, sender, args):
        left = [i for i in self._custom if i not in self._slopes]
        if left:
            self.StatusText.Text = ("{} line(s) still need a gradient - "
                                    "click the orange ones.".format(
                                        len(left)))
            return
        self.result = dict(self._slopes)
        self.Close()

    def on_cancel(self, sender, args):
        self.result = None
        self.Close()


def ask_custom_slopes(xaml_path, lines_mm, custom_idx, preset=None):
    """Show the plan, return {line_index: slope_n} or None on cancel.
    ``preset`` pre-fills already-known slopes (an update run re-asks
    only what it does not know)."""
    win = CustomSlopeWindow(xaml_path, lines_mm, custom_idx,
                            preset=preset)
    win.ShowDialog()
    return win.result
