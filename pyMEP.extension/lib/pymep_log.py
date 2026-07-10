# -*- coding: utf-8 -*-
"""Shared logging - writes to pyRevit output *and* a timestamped file."""

import os
import datetime


class Logger(object):
    """Wrap a pyRevit output object and an optional log file.

    Usage:
        log = Logger(output, "BuildPlanBends")
        log("### Starting")
        ...
        log.close()
    """

    def __init__(self, output, name):
        self._output = output
        log_dir = os.path.join(os.environ.get("APPDATA", ""), "pyRevit", "Logs")
        if not os.path.exists(log_dir):
            try: os.makedirs(log_dir)
            except: pass
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_path = os.path.join(log_dir, "{}_{}.log".format(name, ts))
        try:
            self._lf = open(self.log_path, "w")
            self._lf.write("{}  {}\n{}\n\n".format(
                name, datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "=" * 60))
            self._lf.flush()
        except Exception:
            self._lf = None

    def __call__(self, msg):
        if self._output is not None:
            try: self._output.print_md(msg)
            except: pass
        if self._lf is not None:
            safe = ""
            for ch in msg.replace("**", "").replace("###", "").replace("####", "") \
                         .replace(u"\u2192", "->").replace(u"\u2713", "[OK]"):
                safe += ch if ord(ch) < 128 else "?"
            try:
                self._lf.write(safe + "\n"); self._lf.flush()
            except: pass

    def close(self):
        if self._lf is not None:
            try: self._lf.close()
            except: pass
            self._lf = None
