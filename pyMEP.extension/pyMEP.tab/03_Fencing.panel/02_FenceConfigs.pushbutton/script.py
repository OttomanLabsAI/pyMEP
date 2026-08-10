# -*- coding: utf-8 -*-
"""Fence Configurations - create, edit and delete the named spacing
set-ups used by Place New Fence and Update Fence.

Each configuration: spacing (mm), place-at-endpoints, and a custom
ROTATION in degrees on top of line-aligned (0 follows the line, 90
stands across it). Saved in pyMEP settings, so they survive updates;
Update Fence re-reads them by name, so editing one here re-spaces
its fences on the next update.
"""

__title__  = "Fence\nConfigs"
__author__ = "Glent Group"

import os
import sys

for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

from pyrevit import forms, script

from pymep_config import load_settings, save_settings
import pymep_fence as F

XAML_PATH = os.path.join(
    os.path.dirname(os.path.abspath(sys.modules["pymep_config"].__file__)),
    "pymep_fence_configs.xaml")


class ConfigsWindow(forms.WPFWindow):
    def __init__(self, settings):
        forms.WPFWindow.__init__(self, XAML_PATH)
        self.settings = settings
        self._fill(settings.get(F.SETTINGS_LAST))

    def _fill(self, want):
        cfgs = F.get_configs(self.settings)
        names = sorted(cfgs.keys(), key=lambda s: s.lower())
        self.CmbConfig.Items.Clear()
        for n in names:
            self.CmbConfig.Items.Add(n)
        pick = want if want in cfgs else names[0]
        self.CmbConfig.SelectedIndex = names.index(pick)

    def on_pick(self, sender, args):
        try:
            name = str(self.CmbConfig.SelectedItem or "")
            cfg = F.get_configs(self.settings).get(name)
            if cfg is None:
                return
            self.TxtName.Text = name
            self.TxtSpacing.Text = "{:g}".format(cfg["spacing_mm"])
            self.TxtRotation.Text = "{:g}".format(
                cfg["rotation_deg"])
            self.ChkEnds.IsChecked = bool(cfg["endpoints"])
            self.StatusText.Text = ""
        except Exception:
            pass

    def on_save(self, sender, args):
        try:
            F.upsert_config(self.settings, self.TxtName.Text,
                            self.TxtSpacing.Text,
                            bool(self.ChkEnds.IsChecked),
                            self.TxtRotation.Text)
        except ValueError as ex:
            self.StatusText.Text = str(ex)
            return
        except Exception as ex:
            self.StatusText.Text = str(ex)
            return
        try:
            save_settings(self.settings)
        except Exception:
            pass
        self._fill((self.TxtName.Text or "").strip())
        self.StatusText.Text = ""

    def on_delete(self, sender, args):
        try:
            name = str(self.CmbConfig.SelectedItem or "")
            F.delete_config(self.settings, name)
            try:
                save_settings(self.settings)
            except Exception:
                pass
            self._fill(None)
            self.StatusText.Text = ""
        except Exception as ex:
            self.StatusText.Text = str(ex)

    def on_close(self, sender, args):
        self.Close()


ConfigsWindow(load_settings()).ShowDialog()
script.exit()
