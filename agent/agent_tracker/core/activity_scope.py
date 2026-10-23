"""Identifiers for optional foreground-app duration collection, never window titles."""
from pathlib import PurePosixPath, PureWindowsPath


SYSTEM_NAMES = frozenset({
    'explorer', 'searchhost', 'searchapp', 'startmenuexperiencehost',
    'shellexperiencehost', 'lockapp', 'logonui', 'winlogon', 'dwm',
    'applicationframehost', 'systemsettings', 'taskmgr', 'runtimebroker',
    'sihost', 'textinputhost', 'ctfmon', 'calculatorapp', 'calc', 'notepad',
    'mspaint', 'snippingtool', 'screenclippinghost', 'securityhealthsystray',
    'finder', 'dock', 'windowserver', 'loginwindow', 'systemuiserver',
    'controlcenter', 'spotlight', 'system settings', 'system preferences',
    'screencaptureui', 'notificationcenter', 'universalcontrol',
    'gnome-shell', 'plasmashell', 'xfce4-panel', 'cinnamon', 'mate-panel',
    'softtracking', 'softtrackingui', 'soft tracking', 'soft-tracking',
    'soft-tracking-app', 'soft-tracking-host', 'soft-tracking-host-app',
    'softtrackingapp', 'softtrackinghostapp',
})
BROWSER_NAMES = frozenset({
    'chrome', 'google chrome', 'chromium', 'msedge', 'microsoft edge',
    'firefox', 'opera', 'opera gx', 'brave', 'brave browser', 'vivaldi',
    'browser', 'yandex', 'yandex browser', 'safari',
})
BROWSER_BUNDLES = frozenset({
    'com.google.chrome', 'org.chromium.chromium', 'com.microsoft.edgemac',
    'org.mozilla.firefox', 'com.operasoftware.opera', 'com.operasoftware.operagx',
    'com.brave.browser', 'com.vivaldi.vivaldi', 'ru.yandex.desktop.yandex-browser',
    'com.apple.safari',
})
SYSTEM_BUNDLES = frozenset({
    'com.apple.finder', 'com.apple.dock', 'com.apple.systempreferences',
    'com.apple.calculator', 'com.apple.preview', 'com.apple.textedit',
    'com.apple.terminal', 'com.apple.activitymonitor', 'com.apple.screencaptureui',
    'com.apple.controlcenter', 'com.apple.spotlight', 'com.apple.loginwindow',
})


def automatic_application(active):
    """Return a stable app ID/name, excluding OS UI and browser double counting."""
    executable = active.executable or ''
    path = PureWindowsPath(executable) if '\\' in executable else PurePosixPath(executable)
    name = path.name if executable else (active.process_name or '')
    bundle = active.bundle_id or ''
    if not name and not bundle:
        return None
    if any(len(value) > 255 or any(ord(char) < 32 for char in value) or
           '/' in value or '\\' in value for value in (name, bundle)):
        return None
    stem = name.lower().removesuffix('.exe')
    process = (active.process_name or '').lower().removesuffix('.exe')
    lowered_path = executable.lower().replace('\\', '/')
    if (stem in SYSTEM_NAMES | BROWSER_NAMES or process in SYSTEM_NAMES | BROWSER_NAMES
            or bundle.lower() in BROWSER_BUNDLES
            or lowered_path.startswith(('/system/', '/usr/libexec/'))
            or '/windows/' in lowered_path):
        return None
    # macOS stock apps can also be installed in /Applications instead of /System.
    if bundle.lower() in SYSTEM_BUNDLES or bundle.lower().startswith('com.soft.tracking'):
        return None
    return (bundle or name).lower(), name or bundle
