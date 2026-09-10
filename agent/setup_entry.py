import sys
from pathlib import Path
from agent_tracker.electron_desktop import electron_path

bundle = Path(getattr(sys, '_MEIPASS', Path(__file__).parent)) / 'setup-payload'
if electron_path(bundle / 'electron').is_file():
    from agent_tracker.electron_desktop import installer_main as main
else:
    from agent_tracker.installer import main
raise SystemExit(main())
