# -*- coding: utf-8 -*-
"""Press ENTER to lock in a multi-pick.

Revit's ``Selection.PickObjects`` only ends from the options-bar
'Finish' button - there is no keyboard route. This adds one: while
the pick runs, a background thread watches the keyboard and, the
moment ENTER goes down with Revit in the foreground, finds the
'Finish' button through UI Automation and presses it - so the pick
returns with everything selected, exactly like a mouse click on
Finish.

Everything is FAIL-SOFT: if the keyboard poll, the UI Automation
lookup or the button press is unavailable, nothing breaks - the pick
simply keeps its normal mouse-driven Finish. The watcher arms only
after it has seen ENTER released once, so the keypress that closed a
previous dialog can never finish an empty pick.

Usage:

    with EnterFinishesPick(uiapp):
        refs = uidoc.Selection.PickObjects(...)

IronPython 2.7 / Revit 2022-2026 (English UI button caption, with
the localised captions of the major languages as fallbacks).
"""

VK_RETURN = 0x0D
_POLL_S = 0.05

# options-bar caption of the button that ends a multi-pick
FINISH_CAPTIONS = ("Finish", "Fertig stellen", "Terminer",
                   "Finalizar", "Fine", "Zakoncz",
                   u"完了", u"完成")


class EnterFinishesPick(object):
    """Context manager: ENTER presses the options-bar 'Finish'
    button for the duration of the ``with`` block."""

    def __init__(self, uiapp):
        self._uiapp = uiapp
        self._stop = None

    def __enter__(self):
        try:
            import threading
            self._stop = threading.Event()
            t = threading.Thread(target=self._watch)
            t.setDaemon(True)
            t.start()
        except Exception:
            self._stop = None
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if self._stop is not None:
                self._stop.set()
        except Exception:
            pass
        return False

    # ---- background thread -------------------------------------------
    def _main_hwnd(self):
        try:
            h = self._uiapp.MainWindowHandle    # Revit 2019+
            return int(h.ToInt64()) if hasattr(h, "ToInt64") \
                else int(h)
        except Exception:
            pass
        try:
            from System.Diagnostics import Process
            return int(Process.GetCurrentProcess()
                       .MainWindowHandle.ToInt64())
        except Exception:
            return None

    @staticmethod
    def _same_hwnd(a, b):
        """HWND equality across the signed/unsigned zoo: ctypes
        hands back a SIGNED 32-bit int, IntPtr.ToInt64 an unsigned-
        looking 64-bit one - compare the low 32 bits."""
        try:
            return (int(a) & 0xFFFFFFFF) == (int(b) & 0xFFFFFFFF)
        except Exception:
            return True             # cannot tell - do not block

    def _watch(self):
        import time
        try:
            import ctypes
            u32 = ctypes.windll.user32
        except Exception:
            return
        hwnd = self._main_hwnd()
        armed = False               # need one ENTER-up before firing
        while self._stop is not None and not self._stop.is_set():
            time.sleep(_POLL_S)
            try:
                down = u32.GetAsyncKeyState(VK_RETURN) & 0x8000
            except Exception:
                return
            if not down:
                armed = True
                continue
            if not armed:
                continue
            if hwnd:
                try:
                    if not self._same_hwnd(
                            u32.GetForegroundWindow(), hwnd):
                        continue
                except Exception:
                    pass
            if self._press_finish(hwnd):
                return              # pick is over - thread done
            armed = False           # button not there (yet) - re-arm

    @staticmethod
    def _press_finish(hwnd):
        """Find the options-bar 'Finish' button under the Revit main
        window and invoke it (UI Automation, from THIS background
        thread - UIA client calls must stay off the UI thread)."""
        try:
            import clr
            clr.AddReference("UIAutomationClient")
            clr.AddReference("UIAutomationTypes")
            from System import IntPtr
            from System.Windows.Automation import (
                AndCondition, AutomationElement, ControlType,
                InvokePattern, PropertyCondition,
                PropertyConditionFlags, TreeScope)
            root = None
            if hwnd:
                try:
                    root = AutomationElement.FromHandle(
                        IntPtr(hwnd))
                except Exception:
                    root = None
            if root is None:
                root = AutomationElement.RootElement
            for caption in FINISH_CAPTIONS:
                try:
                    name_cond = PropertyCondition(
                        AutomationElement.NameProperty, caption,
                        PropertyConditionFlags.IgnoreCase)
                except Exception:
                    name_cond = PropertyCondition(
                        AutomationElement.NameProperty, caption)
                cond = AndCondition(
                    PropertyCondition(
                        AutomationElement.ControlTypeProperty,
                        ControlType.Button),
                    name_cond)
                btn = root.FindFirst(TreeScope.Descendants, cond)
                if btn is None:
                    continue
                btn.GetCurrentPattern(
                    InvokePattern.Pattern).Invoke()
                return True
        except Exception:
            pass
        return False
