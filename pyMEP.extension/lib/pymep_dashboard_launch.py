# -*- coding: utf-8 -*-
"""Launch the Civil 3D LandXML Dashboard - plain, or preloaded with the
project's stored data file.

Preloading works by writing a LAUNCH COPY of the viewer HTML with a
one-line script injected into <head>:

    window.__OL_PRELOAD__ = {"name": ..., "text": ...}

The viewer's landing page checks for that global and feeds it through
the exact same buildData path as a browsed/dropped file, so the
dashboard opens straight into the 3D view. The copy lives in the
project's export folder (dashboard_project.html) and is rewritten on
every launch, so it always carries the CURRENT stored file. Everything
is plain file IO - IronPython 2.7 safe, no Revit API."""

import json
import os


def _read_text(path):
    f = open(path, "rb")
    try:
        raw = f.read()
    finally:
        f.close()
    return raw.decode("utf-8-sig", "replace")


def write_preload_html(viewer_path, data_path, out_dir,
                       out_name="dashboard_project.html"):
    """Write the launch copy and return its path."""
    html = _read_text(viewer_path)
    text = _read_text(data_path)
    tag = ('<script>window.__OL_PRELOAD__ = {{"name": {}, "text": {}}};'
           '</script>'.format(json.dumps(os.path.basename(data_path)),
                              json.dumps(text)))
    if "</head>" in html:
        html = html.replace("</head>", tag + "</head>", 1)
    else:
        html = tag + html
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    out = os.path.join(out_dir, out_name)
    f = open(out, "wb")
    try:
        f.write(html.encode("utf-8"))
    finally:
        f.close()
    return out


def launch_html(path):
    """Open ``path`` in the default browser."""
    try:
        from System.Diagnostics import Process
        Process.Start(path)
    except Exception:
        import webbrowser
        webbrowser.open("file:///" + path.replace(os.sep, "/"))
