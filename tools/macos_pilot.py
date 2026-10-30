"""Sealed macOS onedir engine, native entry points and administrator preparation."""
import hashlib
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
APP = 'SOFT Tracking Engine.app'


def command(*args):
    subprocess.run([str(arg) for arg in args], check=True)


def native_architecture(architecture):
    if architecture not in ('arm64', 'x64'):
        raise ValueError('Unsupported macOS architecture')
    return 'x86_64' if architecture == 'x64' else architecture


def engine_spec(architecture='arm64'):
    target = native_architecture(architecture)
    excludes = ['PySide2', 'PySide6', 'PyQt5', 'PyQt6', 'shiboken2', 'shiboken6',
                'agent_tracker.qt_desktop', 'agent_tracker.ui.quick', 'tkinter', '_tkinter']
    hidden = ['_cffi_backend', 'agent_tracker.platform.macos', 'runtime_entry', 'native_entry',
              'bootstrap_entry', 'bootstrap_native_entry', 'setup_entry']
    return '\n'.join([
        'a = Analysis(' + repr([str(ROOT/'agent/macos_engine_entry.py')]) + ',',
        ' pathex=' + repr([str(ROOT/'agent')]) + ', binaries=[],',
        ' datas=' + repr([(str(ROOT/'agent/agent_tracker/assets'), 'agent_tracker/assets')]) + ',',
        ' hiddenimports=' + repr(hidden) + ', excludes=' + repr(excludes) + ')',
        'pyz = PYZ(a.pure)',
        "exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='soft-tracking-engine',",
        " console=True, strip=False, upx=False, target_arch=" + repr(target) + ", codesign_identity='-')",
        "coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='engine')",
        'app = BUNDLE(coll, name=' + repr(APP) + ', icon=' +
        repr(str(ROOT/'agent/agent_tracker/assets/app_light.icns')) + ',',
        " bundle_identifier='com.soft.tracking.engine',",
        " info_plist={'LSMinimumSystemVersion': '13.0', 'LSUIElement': True,",
        " 'CFBundleDevelopmentRegion': 'en', 'CFBundleLocalizations': ['ru', 'en', 'cs', 'uz']})",
    ]) + '\n'


def build_engine(out, work, payload, architecture='arm64'):
    target = native_architecture(architecture)
    spec = work/'macos-engine.spec'
    spec.write_text(engine_spec(architecture), encoding='utf-8')
    dist = out/'engine-dist'
    command(sys.executable, '-m', 'PyInstaller', '--noconfirm', '--distpath', dist,
            '--workpath', work/'engine', spec)
    app = dist/APP
    # BUNDLE seals the framework as a bundle, not merely its Mach-O executable.
    frameworks = sorted((app/'Contents/Frameworks').rglob('*.framework'),
                        key=lambda p: len(p.parts), reverse=True)
    for framework in frameworks:
        if not framework.is_symlink():
            command('/usr/bin/codesign', '--force', '--sign', '-', framework)
    command('/usr/bin/codesign', '--force', '--sign', '-', app)
    command('/usr/bin/codesign', '--verify', '--deep', '--strict', app)
    python = app/'Contents/Frameworks/Python.framework'
    if not list(python.rglob('CodeResources')):
        raise ValueError('Python framework resource seal missing')
    archive = out/'engine.zip'
    command('/usr/bin/ditto', '-c', '-k', '--keepParent', '--norsrc', app, archive)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    name = 'engine-' + digest + '.zip'
    archive = archive.rename(out/name)
    source = (ROOT/'tools/macos_native_launcher.swift').read_text(encoding='utf-8')
    source = source.replace('@@ENGINE_SHA256@@', digest).replace('@@ENGINE_NAME@@', name)
    swift = work/'macos-launcher.swift'
    swift.write_text(source, encoding='utf-8')
    wrapper = out/'native-launcher'
    command('/usr/bin/xcrun', 'swiftc', '-O', '-target', target + '-apple-macos13.0',
            '-o', wrapper, swift)
    command('/usr/bin/codesign', '--force', '--sign', '-', wrapper)
    for folder, names in [('app', ['soft-tracking-app', 'soft-tracking-host-app']),
                          ('launcher', ['soft-tracking', 'soft-tracking-host'])]:
        shutil.copy2(archive, payload/folder/name)
        for executable in names:
            shutil.copy2(wrapper, payload/folder/executable)
    return archive, wrapper


def build_setup(out, setup, archive, wrapper, version):
    from agent_tracker.electron_links import restore_links, MAP_NAME
    app = out/'SOFT-Tracking-Setup.app'
    contents = app/'Contents'
    resources = contents/'Resources'
    (contents/'MacOS').mkdir(parents=True)
    resources.mkdir()
    shutil.copy2(wrapper, contents/'MacOS/SOFT-Tracking-Setup')
    shutil.copy2(archive, resources/archive.name)
    shutil.copytree(setup, resources/'setup-payload')
    # The DMG is read-only. Complete the nested Electron bundle before sealing it.
    electron = resources/'setup-payload/electron'
    restore_links(electron)
    (electron/MAP_NAME).unlink(missing_ok=True)
    icon = ROOT/'agent/agent_tracker/assets/app_light.icns'
    shutil.copy2(icon, resources/'app_light.icns')
    info = {'CFBundleExecutable': 'SOFT-Tracking-Setup', 'CFBundleIdentifier': 'com.soft.tracking.setup',
            'CFBundleName': 'SOFT Tracking Setup', 'CFBundleDisplayName': 'SOFT Tracking Setup',
            'CFBundlePackageType': 'APPL', 'CFBundleIconFile': 'app_light.icns',
            'CFBundleShortVersionString': version.split('-')[0], 'CFBundleVersion': version.split('-')[0],
            'CFBundleDevelopmentRegion': 'en', 'CFBundleLocalizations': ['ru', 'en', 'cs', 'uz'],
            'LSMinimumSystemVersion': '13.0', 'NSHighResolutionCapable': True}
    (contents/'Info.plist').write_bytes(plistlib.dumps(info))
    command('/usr/bin/codesign', '--force', '--sign', '-', app)
    command('/usr/bin/codesign', '--verify', '--deep', '--strict', app)
    image = out/'dmg-content'
    image.mkdir()
    command('/usr/bin/ditto', app, image/app.name)
    shutil.copy2(ROOT/'tools/macos_install.command', image/'Install.command')
    (image/'Install.command').chmod(0o755)
    shutil.copy2(ROOT/'tools/macos_pilot_readme.txt', image/'README.txt')
    return image
