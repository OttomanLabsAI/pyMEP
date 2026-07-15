# -*- coding: utf-8 -*-
"""pyMEP Settings - one WPF window for every setting.

Layout (SettingsWindow.xaml): category sidebar on the left, the picked
category's controls on the right, OK / Cancel / Apply at the bottom -
nothing is written to pyMEP_settings.json until OK or Apply.

Categories:
  General      - folders, Python executable, output window auto-close
  Ducts        - Build Ducts type / system names
  Pipes        - placement names + the LandXML survey origin
  Annotate     - duct label suffix, pipe label offset
  Section Dims - chamber reference-plane dimension pairs
  Updates      - GitHub repo/token + install any tagged version
"""

__title__ = "Settings"
__author__ = "Glent Group"

import os
import sys

# Force-reload pymep_* libs so edits on disk always take effect.
for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

from pyrevit import forms, revit, script

from System.Collections import ArrayList, Hashtable
from System.Windows import Visibility

from pymep_config import (
    load_settings, save_settings, CONFIG_FILE, SCRIPTS_DIR,
    get_export_folder,
    DEFAULT_DUCT_TYPE_NAME, DEFAULT_DUCT_SYSTEM_NAME,
    DEFAULT_PIPE_TYPE_NAME, DEFAULT_PIPE_SYSTEM_NAME,
    DEFAULT_PIPE_HOST_LEVEL,
    DEFAULT_ANNOTATE_SUFFIX, DEFAULT_ANNOTATE_PIPE_OFFSET_MM,
    get_landxml_survey_transform, get_annotate_pipe_offset_mm,
    get_auto_close_output,
    get_chamber_dim_pairs, save_chamber_dim_pairs,
    DEFAULT_CHAMBER_DIM_PAIRS,
    get_local_version, get_github_repo, get_github_token,
    DEFAULT_GITHUB_REPO,
)
import pymep_update as upd

doc = revit.doc
XAML_FILE = script.get_bundle_file("SettingsWindow.xaml")


def _default_pairs():
    return [dict(p) for p in DEFAULT_CHAMBER_DIM_PAIRS]


class SettingsWindow(forms.WPFWindow):

    def __init__(self):
        forms.WPFWindow.__init__(self, XAML_FILE)
        self._pairs = get_chamber_dim_pairs()
        self._versions = []
        self._load_state()
        self._refresh_pairs()
        self.CatList.SelectedIndex = 0

    # ------------------------------------------------------------------
    # state <-> controls
    # ------------------------------------------------------------------
    def _load_state(self):
        s = load_settings()

        # General
        self.TxtScriptFolder.Text = s.get("script_folder", "") or ""
        self.HintScriptFolder.Text = (
            "Blank = the bundled folder: {}".format(SCRIPTS_DIR))
        self.TxtPythonExe.Text = s.get("python_exe", "") or ""
        self.TxtExportOverride.Text = s.get("export_folder_override", "") or ""
        self.ChkAutoClose.IsChecked = get_auto_close_output()
        self._refresh_active_export()

        # Ducts
        self.TxtDuctType.Text = s.get("duct_type_name", "") or ""
        self.HintDuctType.Text = (
            "Rectangular Revit duct type used by Build Ducts. "
            "Blank = default: {}".format(DEFAULT_DUCT_TYPE_NAME))
        self.TxtDuctSystem.Text = s.get("duct_system_type_name", "") or ""
        self.HintDuctSystem.Text = (
            "MEP system type assigned to built ducts. "
            "Blank = default: {}".format(DEFAULT_DUCT_SYSTEM_NAME))

        # Pipes
        self.TxtPipeType.Text = s.get("pipe_type_name", "") or ""
        self.HintPipeType.Text = (
            "Revit pipe type used by Place Pipes. "
            "Blank = default: {}".format(DEFAULT_PIPE_TYPE_NAME))
        self.TxtPipeSystem.Text = s.get("pipe_system_type_name", "") or ""
        self.HintPipeSystem.Text = (
            "Piping system type assigned to placed pipes. "
            "Blank = default: {}".format(DEFAULT_PIPE_SYSTEM_NAME))
        self.TxtPipeLevel.Text = s.get("pipe_host_level", "") or ""
        self.HintPipeLevel.Text = (
            "Level placed pipes are hosted on (elevations still come from "
            "the export's Z values). Blank = default: {}".format(
                DEFAULT_PIPE_HOST_LEVEL))
        self.TxtSegment.Text = s.get("landxml_segment_name", "") or ""
        lx_e, lx_n, lx_z, lx_rot = get_landxml_survey_transform()
        self.TxtLxE.Text = "{:.4f}".format(lx_e)
        self.TxtLxN.Text = "{:.4f}".format(lx_n)
        self.TxtLxZ.Text = "{:.4f}".format(lx_z)
        self.TxtLxRot.Text = "{:.4f}".format(lx_rot)

        # Annotate
        self.TxtAnnSuffix.Text = s.get("annotate_suffix", "") or ""
        self.HintAnnSuffix.Text = (
            "Appended on the second line of the Annotate Ducts label (the "
            "first line is generated, e.g. '3x1 - 3No.200'). "
            "Blank = default: {}".format(DEFAULT_ANNOTATE_SUFFIX))
        self.TxtAnnOffset.Text = "{:g}".format(get_annotate_pipe_offset_mm())
        self.HintAnnOffset.Text = (
            "Perpendicular distance each auto-placed label sits from its "
            "pipe's midpoint, in model mm. Default: {:g}".format(
                DEFAULT_ANNOTATE_PIPE_OFFSET_MM))

        # Section Dims
        self.CmbAxis.SelectedIndex = 0

        # Updates
        self.TxtInstalledVer.Text = get_local_version() or "(no version.txt)"
        self.TxtRepo.Text = get_github_repo()
        self.HintRepo.Text = "Default: {}".format(DEFAULT_GITHUB_REPO)
        self.PwdToken.Password = get_github_token()

    def _refresh_active_export(self):
        try:
            active = get_export_folder(doc) if doc else "(no open document)"
        except Exception:
            active = "(no open document)"
        self.TxtActiveExport.Text = "Active export folder:  {}".format(active)

    def _parse_float(self, textbox, what):
        try:
            return float(textbox.Text.strip())
        except (TypeError, ValueError):
            forms.alert("'{}' is not a valid number for {}.".format(
                textbox.Text, what))
            return None

    def _apply(self):
        """Validate + write everything to pyMEP_settings.json. Returns
        True when saved, False when validation failed (window stays open,
        nothing written)."""
        lx_e = self._parse_float(self.TxtLxE, "Easting E0")
        if lx_e is None:
            self.CatList.SelectedIndex = 2
            return False
        lx_n = self._parse_float(self.TxtLxN, "Northing N0")
        if lx_n is None:
            self.CatList.SelectedIndex = 2
            return False
        lx_z = self._parse_float(self.TxtLxZ, "Base elevation Z0")
        if lx_z is None:
            self.CatList.SelectedIndex = 2
            return False
        lx_rot = self._parse_float(self.TxtLxRot, "Rotation")
        if lx_rot is None:
            self.CatList.SelectedIndex = 2
            return False
        ann_off = self._parse_float(self.TxtAnnOffset, "the label offset")
        if ann_off is None or ann_off < 0:
            if ann_off is not None:
                forms.alert("The label offset must be 0 or more mm.")
            self.CatList.SelectedIndex = 3
            return False

        s = load_settings()
        s["script_folder"] = self.TxtScriptFolder.Text.strip()
        s["python_exe"] = self.TxtPythonExe.Text.strip()
        s["export_folder_override"] = self.TxtExportOverride.Text.strip()
        s["auto_close_output"] = bool(self.ChkAutoClose.IsChecked)

        s["duct_type_name"] = self.TxtDuctType.Text.strip()
        s["duct_system_type_name"] = self.TxtDuctSystem.Text.strip()

        s["pipe_type_name"] = self.TxtPipeType.Text.strip()
        s["pipe_system_type_name"] = self.TxtPipeSystem.Text.strip()
        s["pipe_host_level"] = self.TxtPipeLevel.Text.strip()
        s["landxml_segment_name"] = self.TxtSegment.Text.strip()
        s["landxml_off_e_m"] = lx_e
        s["landxml_off_n_m"] = lx_n
        s["landxml_off_z_m"] = lx_z
        s["landxml_rot_deg"] = lx_rot

        s["annotate_suffix"] = self.TxtAnnSuffix.Text.strip()
        s["annotate_pipe_offset_mm"] = ann_off

        s["github_repo"] = self.TxtRepo.Text.strip()
        s["github_token"] = self.PwdToken.Password.strip()
        save_settings(s)

        # Saving [] keeps the 'fall back to the shipped defaults' baseline,
        # so an unedited default set follows future extension updates.
        if self._pairs == _default_pairs():
            save_chamber_dim_pairs([])
        else:
            save_chamber_dim_pairs(self._pairs)

        self._refresh_active_export()
        self.StatusText.Text = "Saved."
        return True

    # ------------------------------------------------------------------
    # navigation
    # ------------------------------------------------------------------
    def on_category_changed(self, sender, args):
        panels = [self.PanelGeneral, self.PanelDucts, self.PanelPipes,
                  self.PanelAnnotate, self.PanelDims, self.PanelUpdates]
        idx = self.CatList.SelectedIndex
        for i, panel in enumerate(panels):
            panel.Visibility = (
                Visibility.Visible if i == idx else Visibility.Collapsed)

    # ------------------------------------------------------------------
    # General
    # ------------------------------------------------------------------
    def on_browse_script(self, sender, args):
        folder = forms.pick_folder(
            title="Pick the conduit_analysis folder (contains run_analysis.py)")
        if folder:
            self.TxtScriptFolder.Text = folder

    def on_browse_export(self, sender, args):
        folder = forms.pick_folder(
            title="Pick an export folder (overrides the default)")
        if folder:
            self.TxtExportOverride.Text = folder

    def on_clear_export(self, sender, args):
        self.TxtExportOverride.Text = ""

    def on_open_export(self, sender, args):
        try:
            path = get_export_folder(doc) if doc else ""
        except Exception:
            path = ""
        if path and os.path.isdir(path):
            os.startfile(path)
        else:
            forms.alert("Folder does not exist:\n{}".format(
                path or "(no open document)"))

    # ------------------------------------------------------------------
    # Section Dims
    # ------------------------------------------------------------------
    def _refresh_pairs(self, select_index=-1):
        # .NET Hashtables, not Python dicts: the WPF binding engine can
        # only see the indexer on real .NET collection types.
        items = ArrayList()
        for i, p in enumerate(self._pairs):
            row = Hashtable()
            row["num"] = str(i + 1)
            row["label"] = p["label"]
            row["plane_a"] = p["plane_a"]
            row["plane_b"] = p["plane_b"]
            row["axis"] = p["axis"]
            items.Add(row)
        self.LstPairs.ItemsSource = items
        if 0 <= select_index < items.Count:
            self.LstPairs.SelectedIndex = select_index

    def _pair_from_fields(self):
        pa = self.TxtPlaneA.Text.strip()
        pb = self.TxtPlaneB.Text.strip()
        if not pa or not pb:
            forms.alert("Both reference-plane names (Plane A and Plane B) "
                        "are needed.")
            return None
        axis = "height" if self.CmbAxis.SelectedIndex == 1 else "width"
        label = self.TxtPairLabel.Text.strip() or "{} / {}".format(pa, pb)
        return {"label": label, "plane_a": pa, "plane_b": pb, "axis": axis}

    def on_pair_select(self, sender, args):
        idx = self.LstPairs.SelectedIndex
        if idx < 0 or idx >= len(self._pairs):
            return
        p = self._pairs[idx]
        self.TxtPairLabel.Text = p["label"]
        self.TxtPlaneA.Text = p["plane_a"]
        self.TxtPlaneB.Text = p["plane_b"]
        self.CmbAxis.SelectedIndex = 1 if p["axis"] == "height" else 0

    def on_pair_add(self, sender, args):
        pair = self._pair_from_fields()
        if pair:
            self._pairs.append(pair)
            self._refresh_pairs(len(self._pairs) - 1)

    def on_pair_update(self, sender, args):
        idx = self.LstPairs.SelectedIndex
        if idx < 0 or idx >= len(self._pairs):
            forms.alert("Pick the pair to update in the list first.")
            return
        pair = self._pair_from_fields()
        if pair:
            self._pairs[idx] = pair
            self._refresh_pairs(idx)

    def on_pair_remove(self, sender, args):
        idx = self.LstPairs.SelectedIndex
        if idx < 0 or idx >= len(self._pairs):
            forms.alert("Pick the pair to remove in the list first.")
            return
        del self._pairs[idx]
        self._refresh_pairs(min(idx, len(self._pairs) - 1))

    def on_pair_reset(self, sender, args):
        self._pairs = _default_pairs()
        self._refresh_pairs()

    # ------------------------------------------------------------------
    # Updates
    # ------------------------------------------------------------------
    def on_load_versions(self, sender, args):
        repo = self.TxtRepo.Text.strip() or DEFAULT_GITHUB_REPO
        token = self.PwdToken.Password.strip()
        self.StatusText.Text = "Contacting GitHub..."
        try:
            self._versions = upd.list_versions(repo, token)
        except Exception as ex:
            self._versions = []
            self.StatusText.Text = ""
            forms.alert(
                "Couldn't list versions from GitHub ({}):\n\n{}\n\nIf the "
                "repository is private, fill in the access token above."
                .format(repo, ex))
            return
        cur = get_local_version()
        self.CmbVersions.Items.Clear()
        for v in self._versions:
            self.CmbVersions.Items.Add(
                v + ("   (installed)" if v == cur else ""))
        if self._versions:
            self.CmbVersions.SelectedIndex = 0
            self.StatusText.Text = "{} versions found.".format(
                len(self._versions))
        else:
            self.StatusText.Text = ""
            forms.alert("No tagged versions found on {}.".format(repo))

    def on_install_version(self, sender, args):
        idx = self.CmbVersions.SelectedIndex
        if idx < 0 or idx >= len(self._versions):
            forms.alert("Click 'Load versions' and pick a version first.")
            return
        ver = self._versions[idx]
        repo = self.TxtRepo.Text.strip() or DEFAULT_GITHUB_REPO
        token = self.PwdToken.Password.strip()
        cur = get_local_version()
        if forms.alert(
                "Install {} over the live extension (currently {})?\n\n"
                "The current version's folder is removed after a successful "
                "install - every version stays reinstallable from here."
                .format(ver, cur or "(no version.txt)"),
                title="Install version",
                options=["Install", "Cancel"]) != "Install":
            return
        self.StatusText.Text = "Downloading {}...".format(ver)
        zip_path = upd.download_extension_zip(
            ver, upd.zip_url_for(repo, ver), repo=repo, token=token)
        if zip_path is None:
            self.StatusText.Text = ""
            forms.alert(
                "Download of {} failed - nothing was changed.\n\nIf the "
                "repository is private, fill in the access token above."
                .format(ver))
            return
        try:
            new_ver = upd.deploy_zip(zip_path)
        except Exception as ex:
            self.StatusText.Text = ""
            forms.alert("{}".format(ex))
            return
        self.TxtInstalledVer.Text = new_ver or ver
        self.StatusText.Text = "Installed {}.".format(new_ver or ver)
        if forms.alert(
                "Installed {}.\n\nReload pyRevit now so it is live?"
                .format(new_ver or ver),
                title="Installed",
                options=["Reload pyRevit", "Later"]) == "Reload pyRevit":
            self.Close()
            try:
                from pyrevit.loader import sessionmgr
                sessionmgr.reload_pyrevit()
            except Exception as ex:
                forms.alert(
                    "Automatic reload failed ({}).\n\nReload manually: "
                    "pyRevit tab > Reload.".format(ex))

    # ------------------------------------------------------------------
    # bottom bar
    # ------------------------------------------------------------------
    def on_open_settings_file(self, sender, args):
        if not os.path.exists(CONFIG_FILE):
            save_settings(load_settings())
        try:
            os.startfile(CONFIG_FILE)
        except Exception as ex:
            forms.alert("Couldn't open {}:\n{}".format(CONFIG_FILE, ex))

    def on_ok(self, sender, args):
        if self._apply():
            self.Close()

    def on_apply(self, sender, args):
        self._apply()

    def on_cancel(self, sender, args):
        self.Close()


SettingsWindow().ShowDialog()
