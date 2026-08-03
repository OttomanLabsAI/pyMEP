# -*- coding: utf-8 -*-
"""Per-project file store - the files a model's workflows depend on,
kept together in one managed folder with a small registry.

Layout (inside the model's export folder, so it follows the project):

    <exports>/<model>/project_files/
        project_files.json      <- the registry: slot -> stored filename
        <the stored files themselves>

Files are COPIED in (the original stays where it was), stored under
their own filename, and addressed by SLOT - a named role the buttons
look up. The original's path is remembered: refresh_slot() re-copies
it when it has changed since, so consumers (the dashboard launch)
always see the file's CURRENT content. Slots defined today:

    dashboard_data   the Civil 3D LandXML the Export Dashboard opens by
                     default (the dashboard's own MODEL-*.json exports
                     can be stored here too for safekeeping, but the
                     dashboard preloads LandXML)

This module is deliberately REVIT-FREE (os/json/shutil only) so it runs
and unit-tests under CPython as-is; the Project Files button resolves
the base folder from the document and calls in. IronPython 2.7 safe.
"""

import datetime
import json
import os
import shutil


REGISTRY = "project_files.json"

# (slot key, label shown in the window) - extend as workflows grow
SLOTS = [
    ("dashboard_data", "Dashboard data (Civil 3D LandXML)"),
]


def slot_label(slot):
    for k, lbl in SLOTS:
        if k == slot:
            return lbl
    return slot


def ensure_dir(base):
    if not os.path.isdir(base):
        os.makedirs(base)
    return base


def _registry_path(base):
    return os.path.join(base, REGISTRY)


def load_registry(base):
    """The registry dict ({"slots": {slot: {"file": name, "added":
    iso}}}); missing/corrupt -> a fresh empty one (never raises)."""
    try:
        with open(_registry_path(base), "r") as f:
            reg = json.load(f)
        if isinstance(reg, dict) and isinstance(reg.get("slots"), dict):
            return reg
    except Exception:
        pass
    return {"slots": {}}


def save_registry(base, reg):
    ensure_dir(base)
    with open(_registry_path(base), "w") as f:
        json.dump(reg, f, indent=2, sort_keys=True)


def store_file(base, slot, src_path):
    """Copy ``src_path`` into the store and point ``slot`` at it.
    Replaces the slot's previous file (the previous copy is deleted when
    no other slot still references it). The ORIGINAL's path is recorded
    so refresh_slot can re-copy it when it changes - a re-exported
    LandXML reaches the dashboard without a manual re-store. Returns
    the stored path."""
    if not os.path.isfile(src_path):
        raise ValueError("Not a file: {}".format(src_path))
    ensure_dir(base)
    name = os.path.basename(src_path)
    dest = os.path.join(base, name)
    reg = load_registry(base)
    old = reg["slots"].get(slot, {}).get("file")
    if os.path.abspath(src_path) != os.path.abspath(dest):
        shutil.copy2(src_path, dest)
    reg["slots"][slot] = {"file": name,
                          "source": os.path.abspath(src_path),
                          "added": datetime.datetime.now().isoformat()}
    save_registry(base, reg)
    if old and old != name:
        _delete_if_unreferenced(base, reg, old)
    return dest


def _source_is_newer(entry, dest):
    """True when the slot's recorded original exists, is a different
    file, and has changed since it was copied in (newer mtime or a
    different size). None when there is no usable source to compare."""
    src = entry.get("source")
    if not src or not os.path.isfile(src):
        return None
    if os.path.abspath(src) == os.path.abspath(dest):
        return False
    try:
        s, d = os.stat(src), os.stat(dest)
    except Exception:
        return None
    return s.st_mtime > d.st_mtime + 1.0 or s.st_size != d.st_size


def refresh_slot(base, slot):
    """Bring the slot's stored copy up to date with the file it was
    stored FROM, then return (path, status). The dashboard launches
    through this, so an XML re-exported over the original shows its
    NEW content without re-storing it by hand. Status:

    'empty'     - slot unset, or the stored copy is gone from disk
    'fresh'     - the stored copy already matches the original
    'refreshed' - the original changed -> stored copy re-copied
    'no_source' - the original is unknown (stored by an older pyMEP)
                  or has moved/renamed - the stored copy is used as-is
    'failed'    - the original changed but could not be re-copied
                  (locked/unreadable) - the OLD stored copy is used
    """
    reg = load_registry(base)
    entry = reg["slots"].get(slot) or {}
    name = entry.get("file")
    if not name:
        return None, "empty"
    dest = os.path.join(base, name)
    if not os.path.isfile(dest):
        return None, "empty"
    newer = _source_is_newer(entry, dest)
    if newer is None:
        return dest, "no_source"
    if not newer:
        return dest, "fresh"
    try:
        shutil.copy2(entry["source"], dest)
        entry["added"] = datetime.datetime.now().isoformat()
        reg["slots"][slot] = entry
        save_registry(base, reg)
        return dest, "refreshed"
    except Exception:
        return dest, "failed"


def slot_file(base, slot):
    """The stored file's absolute path, or None (unset, or the file has
    gone missing on disk)."""
    reg = load_registry(base)
    name = reg["slots"].get(slot, {}).get("file")
    if not name:
        return None
    path = os.path.join(base, name)
    return path if os.path.isfile(path) else None


def remove_slot(base, slot):
    """Unset the slot and delete its stored copy (when nothing else
    references it). Returns True when something was removed."""
    reg = load_registry(base)
    entry = reg["slots"].pop(slot, None)
    if entry is None:
        return False
    save_registry(base, reg)
    name = entry.get("file")
    if name:
        _delete_if_unreferenced(base, reg, name)
    return True


def _delete_if_unreferenced(base, reg, name):
    for e in reg["slots"].values():
        if e.get("file") == name:
            return
    try:
        os.remove(os.path.join(base, name))
    except Exception:
        pass


def list_entries(base):
    """One row per defined slot: [(slot, label, filename or None,
    exists_on_disk, source_is_newer), ...] - what the window shows.
    ``source_is_newer`` flags a stored copy whose original has changed
    since (the dashboard refreshes it automatically on launch)."""
    reg = load_registry(base)
    out = []
    for slot, lbl in SLOTS:
        entry = reg["slots"].get(slot, {})
        name = entry.get("file")
        dest = os.path.join(base, name) if name else None
        exists = bool(name) and os.path.isfile(dest)
        stale = bool(exists and _source_is_newer(entry, dest))
        out.append((slot, lbl, name, exists, stale))
    return out
