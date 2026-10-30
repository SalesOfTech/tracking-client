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
    if '--locale-self-test' in sys.argv:
        return locale_self_test()
    health_phase('setup-entry')
    from agent_tracker.electron_desktop import electron_path
    health_phase('setup-imported')
    from agent_tracker.setup_payload import setup_payload
    bundle = setup_payload()
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


def locale_self_test():
    import json
    import platform
    import psutil
    from agent_tracker import i18n

    marker = os.environ.get('SOFT_TRACKING_LOCALE_SMOKE')
    if not marker or sys.platform != 'darwin' or not getattr(sys, 'frozen', False):
        raise ValueError('Frozen macOS locale smoke marker required')
    # Call CF directly as well as the public selector: fallback must not mask a broken bridge.
    tags, region = i18n._macos_locale()
    system_tags, system_region = i18n.system_locale()
    result = {
        'frozen': True, 'uid': os.getuid(), 'euid': os.geteuid(),
        'executable': sys.executable, 'process_executable': psutil.Process().exe(),
        'architecture': platform.machine(),
        'frozen_root': str(sys._MEIPASS), 'i18n_file': i18n.__file__,
        'native_tags': tags, 'native_region': region,
        'system_tags': system_tags, 'system_region': system_region,
        'detected': i18n.detect_language(),
        'automatic_enrollment': i18n.client_language('', {'language': 'en'}),
        'languages': dict(i18n.LANGUAGES),
        'install_labels': {key: i18n.translate(key, 'install') for key in i18n.LANGUAGES},
        'overrides': {key: i18n.detect_language(key) for key in i18n.LANGUAGES},
        'saved_overrides': {key: i18n.client_language(key) for key in i18n.LANGUAGES},
        'manual_enrollment': {
            key: i18n.client_language('', {'language': key, 'language_source': 'manual'})
            for key in i18n.LANGUAGES},
    }
    with Path(marker).open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=True, sort_keys=True)
    return 0


def entrypoint():
    try:
        return run()
    except Exception as error:
        marker = (os.environ.get('SOFT_TRACKING_LOCALE_SMOKE') if '--locale-self-test' in sys.argv else
                  os.environ.get('SOFT_TRACKING_HEALTH') if '--health-check' in sys.argv else
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
