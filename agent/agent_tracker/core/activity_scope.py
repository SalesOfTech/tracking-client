"""Identifiers for optional foreground-app duration collection, never window titles."""
from pathlib import PurePosixPath, PureWindowsPath
import re


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
    'cmd', 'powershell', 'pwsh', 'conhost', 'windowsterminal',
    'bash', 'sh', 'zsh', 'dash', 'fish', 'kscreenlocker_greet',
    'gnome-screensaver', 'xfce4-screensaver', 'xscreensaver',
    'softtracking', 'softtrackingui', 'soft tracking', 'soft-tracking',
    'soft-tracking-app', 'soft-tracking-host', 'soft-tracking-host-app',
    'softtrackingapp', 'softtrackinghostapp',
})
BROWSER_NAMES = frozenset({
    'chrome', 'google chrome', 'chromium', 'msedge', 'microsoft edge',
    'firefox', 'opera', 'opera gx', 'brave', 'brave browser', 'vivaldi',
    'browser', 'yandex', 'yandex browser', 'safari',
    'google-chrome', 'google-chrome-stable', 'chromium-browser',
    'microsoft-edge', 'microsoft-edge-stable', 'firefox-esr', 'brave-browser',
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
    'com.apple.systemuiserver', 'org.gnome.shell', 'org.kde.plasmashell',
})


def valid_identifier(value):
    # Match the server's wire contract before creating an outbox entry.
    return (bool(value) and len(value.encode('utf-8')) <= 160
            and value[0].isalnum() and value.strip() == value
            and '..' not in value and not value.lower().startswith('v3-other-')
            and not re.search(r'\s--?[^\W_]', value)
            and all(char.isalnum() or char in ' ._()+-' for char in value))


def automatic_application(active):
    """Return a stable app ID/name, excluding OS UI and browser double counting."""
    executable = active.executable or ''
    path = PureWindowsPath(executable) if '\\' in executable else PurePosixPath(executable)
    name = path.name if executable else (active.process_name or '')
    bundle = active.bundle_id or ''
    if not name and not bundle:
        return None
    if any(value and not valid_identifier(value) for value in (name, bundle)):
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
