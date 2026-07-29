# -*- coding: utf-8 -*-
"""Network Settings - configure the Networks dashboard.

One dialog for everything the Networks buttons need: the filter word
that makes a placed family an INPUT NODE (families whose FAMILY name
contains it; the instance's type name names its network), the folder
Apply Edits reads the dashboard's saved edits file from (blank =
Downloads), and whether Apply Edits asks before changing the model.
Saved per user in the pyMEP settings.
"""

__title__  = "Network Settings"
__author__ = "Glent Group"

import os
import sys

for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

from pyrevit import forms, script

from pymep_config import load_settings, save_settings
from pymep_drainage_networks import networks_settings, FILTER_DEFAULT

XAML_PATH = os.path.join(
    os.path.dirname(os.path.abspath(sys.modules["pymep_config"].__file__)),
    "pymep_networks_settings.xaml")

settings = load_settings()
filt, folder, confirm = networks_settings(settings)


class NetworkSettingsWindow(forms.WPFWindow):

    def __init__(self):
        forms.WPFWindow.__init__(self, XAML_PATH)
        self.result = None
        self.TxtFilter.Text = filt
        self.TxtEditsDir.Text = folder
        self.ChkConfirm.IsChecked = confirm

    def on_ok(self, sender, args):
        f = self.TxtFilter.Text.strip() or FILTER_DEFAULT
        d = self.TxtEditsDir.Text.strip()
        if d and not os.path.isdir(d):
            forms.alert("The edits folder doesn't exist:\n\n{}\n\n"
                        "Fix the path or leave it blank for "
                        "Downloads.".format(d))
            return
        self.result = {"networks_filter": f,
                       "networks_edits_folder": d,
                       "networks_confirm_apply":
                       bool(self.ChkConfirm.IsChecked)}
        self.Close()

    def on_cancel(self, sender, args):
        self.result = None
        self.Close()


win = NetworkSettingsWindow()
win.ShowDialog()
if win.result is None:
    script.exit()

settings.update(win.result)
save_settings(settings)
