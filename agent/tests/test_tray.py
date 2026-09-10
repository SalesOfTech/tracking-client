import sys
import unittest
from pathlib import Path
from types import ModuleType,SimpleNamespace
from unittest.mock import Mock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent_tracker.ui import tray


class TrayTests(unittest.TestCase):
    def test_no_x11_tray_does_not_start_a_background_icon(self):
        display=Mock()
        display.get_default_screen.return_value=0
        display.get_selection_owner.return_value=0
        module=ModuleType('Xlib.display');module.Display=Mock(return_value=display)
        icon=Mock();icon.__module__='pystray._xorg'
        with patch.object(tray,'pystray',SimpleNamespace(Icon=icon)),patch.dict(sys.modules,{'Xlib':ModuleType('Xlib'),'Xlib.display':module}):
            controller=tray.TrayController('Test',[])
            self.assertFalse(controller.start())
            self.assertIsNone(controller._monitor_thread)
            icon.assert_not_called()
            display.close.assert_called_once()


if __name__=='__main__':unittest.main()
