import os
import sys


def run():
    if "--supervisor" in sys.argv:
        import argparse
        from agent_tracker.supervisor import main
        parser = argparse.ArgumentParser()
        parser.add_argument("--supervisor", action="store_true")
        parser.add_argument("--installed-root", required=True)
        parser.add_argument("--autostart", action="store_true")
        args = parser.parse_args()
        return main(args.installed_root, args.autostart)
    from agent_tracker.electron_desktop import available
    if available():
        from agent_tracker.electron_desktop import main
    else:
        from agent_tracker.qt_desktop import main
    return main()


try:
    result=run()
except Exception:
    if '--health-check' not in sys.argv or not os.environ.get('SOFT_TRACKING_HEALTH'):
        raise
    import traceback
    from pathlib import Path
    Path(os.environ['SOFT_TRACKING_HEALTH']+'.error').write_text(traceback.format_exc(),encoding='utf-8')
    result=1
raise SystemExit(result)
