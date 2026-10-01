"""CI-only onefile harness; not installed or shipped as an uninstall entry point."""
import sys

from agent_tracker.uninstaller_smoke import run


if sys.argv[1:] != ['--uninstall-self-test']:
    raise SystemExit(2)
raise SystemExit(run(require_onefile=True))
