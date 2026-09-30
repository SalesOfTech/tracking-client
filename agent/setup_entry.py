import os
import sys
from pathlib import Path


def health_phase(phase):
    if '--health-check' in sys.argv and os.environ.get('SOFT_TRACKING_HEALTH'):
        try:
            with Path(os.environ['SOFT_TRACKING_HEALTH'] + '.phase').open('a', encoding='utf-8') as stream:
                stream.write(phase + '\n')
        except OSError:
            pass


def run():
    health_phase('setup-entry')
    from agent_tracker.electron_desktop import electron_path
    health_phase('setup-imported')
    bundle = Path(getattr(sys, '_MEIPASS', Path(__file__).parent)) / 'setup-payload'
    if '--migration-self-test' in sys.argv:
        from agent_tracker.migration_smoke import run_automatic
        from agent_tracker.core.files import atomic_json
        marker = os.environ.get('SOFT_TRACKING_MIGRATION_SMOKE')
        if not marker:
            raise ValueError('Isolated migration smoke marker required')
        run_automatic(bundle)
        atomic_json(Path(marker), {'ok': True, 'isolated': True})
        return 0
    if electron_path(bundle / 'electron').is_file():
        from agent_tracker.electron_desktop import installer_main as main
        health_phase('setup-electron')
    else:
        from agent_tracker.installer import main
        health_phase('setup-legacy')
    return main()


def entrypoint():
    try:
        return run()
    except Exception as error:
        marker = (os.environ.get('SOFT_TRACKING_HEALTH') if '--health-check' in sys.argv else
                  os.environ.get('SOFT_TRACKING_MIGRATION_SMOKE') if '--migration-self-test' in sys.argv else None)
        if not marker:
            raise
        # Do not let PyInstaller's windowed exception dialog hide a failed health check.
        # Exception messages, source lines and locals can contain enrollment credentials.
        import traceback
        frames = traceback.extract_tb(error.__traceback__)
        diagnostic = type(error).__name__ + '\n' + '\n'.join(
            '{}:{}:{}'.format(Path(frame.filename).name, frame.lineno, frame.name) for frame in frames)
        try:
            Path(marker + '.error').write_text(diagnostic + '\n', encoding='utf-8')
        except OSError:
            pass
        health_phase('setup-failed')
        return 1


if __name__ == '__main__':
    raise SystemExit(entrypoint())
