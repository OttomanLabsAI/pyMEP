# -*- coding: utf-8 -*-
"""Project Files - the file-management window for THIS project's files.

The files a model's workflows depend on live together in one managed
folder (<exports>/<model>/project_files/), copied in and addressed by
role. First resident: the Civil 3D LandXML the Export Dashboard opens
by default. Set/replace, open, or remove each file here; the other
pyMEP buttons pick them up automatically.
"""

__title__  = "Project\nFiles"
__author__ = "Glent Group"

import os
import sys

# Force-reload pymep_* libs so edits on disk always take effect.
for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

from pyrevit import revit, forms, script

import pymep_project_files as pf
from pymep_config import get_export_folder

doc = revit.doc
BASE = os.path.join(get_export_folder(doc), "project_files")

XAML_PATH = os.path.join(
    os.path.dirname(os.path.abspath(sys.modules["pymep_config"].__file__)),
    "pymep_project_files.xaml")

from System.Collections import ArrayList, Hashtable


class FilesWindow(forms.WPFWindow):

    def __init__(self):
        forms.WPFWindow.__init__(self, XAML_PATH)
        self.TxtProject.Text = "{}  -  {}".format(doc.Title, BASE)
        self._refresh()

    # ---- list plumbing ----------------------------------------------------
    def _refresh(self):
        rows = ArrayList()
        for slot, label, name, exists in pf.list_entries(BASE):
            h = Hashtable()
            h["slot"] = slot
            h["label"] = label
            h["file"] = name or "(not set)"
            h["status"] = ("Stored" if exists
                           else ("MISSING" if name else "-"))
            rows.Add(h)
        self.LstFiles.ItemsSource = rows
        if rows.Count:
            self.LstFiles.SelectedIndex = 0

    def _selected_slot(self):
        item = self.LstFiles.SelectedItem
        if item is None:
            self.StatusText.Text = "Pick a row first."
            return None
        return item["slot"]

    # ---- buttons ----------------------------------------------------------
    def on_set(self, sender, args):
        slot = self._selected_slot()
        if slot is None:
            return
        picked = forms.pick_file(
            files_filter="Dashboard data (*.xml;*.json)|*.xml;*.json|"
                         "All files (*.*)|*.*",
            title="Pick the file to store for: {}".format(
                pf.slot_label(slot)))
        if not picked:
            return
        try:
            pf.store_file(BASE, slot, picked)
            self.StatusText.Text = ""
        except Exception as ex:
            self.StatusText.Text = "Couldn't store the file: {}".format(ex)
        self._refresh()

    def on_open(self, sender, args):
        slot = self._selected_slot()
        if slot is None:
            return
        path = pf.slot_file(BASE, slot)
        if not path:
            self.StatusText.Text = "Nothing stored for that row yet."
            return
        try:
            from System.Diagnostics import Process
            Process.Start(path)
        except Exception as ex:
            self.StatusText.Text = "Couldn't open it: {}".format(ex)

    def on_folder(self, sender, args):
        try:
            pf.ensure_dir(BASE)
            from System.Diagnostics import Process
            Process.Start(BASE)
        except Exception as ex:
            self.StatusText.Text = "Couldn't open the folder: {}".format(ex)

    def on_remove(self, sender, args):
        slot = self._selected_slot()
        if slot is None:
            return
        if pf.remove_slot(BASE, slot):
            self.StatusText.Text = ""
        else:
            self.StatusText.Text = "Nothing stored for that row."
        self._refresh()

    def on_close(self, sender, args):
        self.Close()


FilesWindow().ShowDialog()
