from __future__ import annotations

import logging
import platform
import sys
import threading
from dataclasses import dataclass
from typing import Callable, Optional

try:
    import pystray
    from PIL import Image, ImageDraw
except ImportError:  # pragma: no cover - tray icon optional
    pystray = None

if sys.platform == "darwin":
    try:
        from Foundation import NSUserDefaults  # type: ignore
    except Exception:  # pragma: no cover - optional on macOS
        NSUserDefaults = None
else:  # pragma: no cover
    NSUserDefaults = None

LOG = logging.getLogger(__name__)


@dataclass
class TrayAction:
    label: str
    callback: Callable[[], None]


class TrayController:
    def __init__(
        self,
        title: str,
        actions: list[TrayAction],
        icon_light: Optional["Image.Image"] = None,
        icon_dark: Optional["Image.Image"] = None,
    ) -> None:
        self.title = title
        self.actions = actions
        self._icon: Optional["pystray.Icon"] = None
        self._thread: Optional[threading.Thread] = None
        self._monitor_thread: Optional[threading.Thread] = None
        self._stop_monitor = threading.Event()
        self._icon_light = icon_light or self._build_icon(dark=False)
        self._icon_dark = icon_dark or self._build_icon(dark=True)
        self._current_dark = False

    @staticmethod
    def is_supported() -> bool:
        return pystray is not None

    def start(self, block: bool = False) -> bool:
        if pystray is None:
            LOG.warning("pystray not installed, tray icon disabled")
            return False

        if pystray.Icon.__module__ == 'pystray._xorg':
            from Xlib.display import Display
            display = Display()
            try:
                atom = display.intern_atom('_NET_SYSTEM_TRAY_S' + str(display.get_default_screen()))
                if not display.get_selection_owner(atom):
                    LOG.info('No X11 system tray; keep the application window available')
                    return False
            finally:
                display.close()

        self._stop_monitor.clear()
        self._current_dark = self._is_dark_mode()
        selected_icon = self._icon_dark if self._current_dark else self._icon_light

        def make_handler(callback: Callable[[], None]):
            def _handler(icon, item):
                callback()

            return _handler

        menu_items = [
            pystray.MenuItem(action.label, make_handler(action.callback), default=index == 0)
            for index, action in enumerate(self.actions)
        ]
        menu = pystray.Menu(*menu_items)
        self._icon = pystray.Icon(self.title, selected_icon, menu=menu)
        self._start_theme_monitor()

        if block and sys.platform == "darwin":
            self._icon.run()
            return True

        if hasattr(self._icon, "run_detached"):
            self._icon.run_detached()
        else:
            self._thread = threading.Thread(target=self._icon.run, daemon=True)
            self._thread.start()
        return True

    def stop(self) -> None:
        if self._icon:
            self._icon.stop()
        self._stop_monitor.set()
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=1)

    def _build_icon(self, dark: bool = False):
        background = (32, 34, 40) if dark else (235, 241, 248)
        foreground = "white" if dark else "black"
        image = Image.new("RGB", (64, 64), color=background)
        draw = ImageDraw.Draw(image)
        draw.rectangle((10, 10, 54, 54), outline=foreground)
        draw.text((20, 22), "SA", fill=foreground)
        return image

    def _start_theme_monitor(self) -> None:
        if not (self._icon_light and self._icon_dark):
            return
        self._monitor_thread = threading.Thread(target=self._theme_monitor_loop, daemon=True)
        self._monitor_thread.start()

    def _theme_monitor_loop(self) -> None:
        while not self._stop_monitor.wait(1):
            new_dark = self._is_dark_mode()
            if new_dark != self._current_dark:
                self._current_dark = new_dark
                self._update_icon()

    def _update_icon(self) -> None:
        if not self._icon:
            return
        new_icon = self._icon_dark if self._current_dark else self._icon_light
        if new_icon:
            self._icon.icon = new_icon

    def _is_dark_mode(self) -> bool:
        system = platform.system().lower()
        if system == "windows":
            try:
                import winreg  # type: ignore

                path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
                    value, _ = winreg.QueryValueEx(key, "SystemUsesLightTheme")
                return value == 0
            except Exception:
                return False
        if system == "darwin" and NSUserDefaults is not None:
            try:
                defaults = NSUserDefaults.standardUserDefaults()
                style = defaults.stringForKey_("AppleInterfaceStyle")
                return style == "Dark"
            except Exception:
                return False
        return False
