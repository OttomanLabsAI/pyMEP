"""Scan export folder for pipe/fitting CSV sets."""
import os
import re
import glob


def find_export_sets(export_folder):
    """Find all pipe/fitting CSV pairs in the export folder.
    
    Returns list of dicts sorted newest first:
    [{'timestamp': '20260417_095106', 
      'pipes': 'full/path/pipes_20260417_095106.csv',
      'fittings': 'full/path/fittings_20260417_095106.csv',
      'label': '2026-04-17 09:51:06'}, ...]
    """
    pipe_files = glob.glob(os.path.join(export_folder, "pipes_*.csv"))
    fitting_files = glob.glob(os.path.join(export_folder, "fittings_*.csv"))

    # Extract timestamps
    pipe_ts = {}
    for f in pipe_files:
        m = re.search(r"pipes_(\d{8}_\d{6})\.csv$", f)
        if m:
            pipe_ts[m.group(1)] = f

    fit_ts = {}
    for f in fitting_files:
        m = re.search(r"fittings_(\d{8}_\d{6})\.csv$", f)
        if m:
            fit_ts[m.group(1)] = f

    # Find matching pairs
    sets = []
    all_timestamps = sorted(set(pipe_ts.keys()) | set(fit_ts.keys()), reverse=True)

    for ts in all_timestamps:
        # Format timestamp for display: 20260417_095106 -> 2026-04-17 09:51:06
        label = "{}-{}-{} {}:{}:{}".format(ts[0:4], ts[4:6], ts[6:8],
                                            ts[9:11], ts[11:13], ts[13:15])
        entry = {
            "timestamp": ts,
            "label": label,
            "pipes": pipe_ts.get(ts),
            "fittings": fit_ts.get(ts),
        }

        # Count rows for display
        if entry["pipes"] and os.path.exists(entry["pipes"]):
            with open(entry["pipes"]) as f:
                entry["n_pipes"] = sum(1 for _ in f) - 1
        else:
            entry["n_pipes"] = 0

        if entry["fittings"] and os.path.exists(entry["fittings"]):
            with open(entry["fittings"]) as f:
                entry["n_fittings"] = sum(1 for _ in f) - 1
        else:
            entry["n_fittings"] = 0

        sets.append(entry)

    return sets


def format_dropdown_options(sets):
    """Format export sets as dropdown option strings.
    
    Returns dict of {display_label: timestamp}
    """
    options = {}
    for s in sets:
        parts = []
        if s["n_pipes"]:
            parts.append("{} pipes".format(s["n_pipes"]))
        if s["n_fittings"]:
            parts.append("{} fittings".format(s["n_fittings"]))
        detail = ", ".join(parts) if parts else "empty"
        key = "{} ({})".format(s["label"], detail)
        options[key] = s["timestamp"]
    return options
