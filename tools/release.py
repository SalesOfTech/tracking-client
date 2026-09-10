"""Native GitHub runner builds; never cross-label binaries across OS/architecture."""
from __future__ import annotations
import argparse
import base64
import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT / "agent"))
from agent_tracker.core.files import atomic_json
from agent_tracker.electron_links import MAP_NAME, MAX_MAP_BYTES, plan_links

ELECTRON_VERSION = '44.3.0'
ELECTRON_PLATFORMS = {'windows': 'win32', 'macos': 'darwin', 'linux': 'linux'}
# Existing 3.0.4/3.1.0 verifiers must be able to stage the transition package.
TRANSITION_ARCHIVE_BYTES = 250 * 1024 * 1024
TRANSITION_UNPACKED_BYTES = 750 * 1024 * 1024


def require_github_runner():
    if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
        raise RuntimeError('Native release builds run only on GitHub-hosted Actions runners')


def build_plan(args):
    import re
    from agent_tracker.core.target import runtime_target
    local_test = getattr(args, 'local_test', False) is True
    if not local_test:
        require_github_runner()
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-(?:alpha|beta|rc)\.\d+)?', args.version):
        raise ValueError('Invalid version')
    os_name, arch = runtime_target()
    if local_test:
        if (platform.system() != 'Windows' or platform.machine().lower() not in ('amd64', 'x64', 'x86_64')
                or sys.maxsize <= 2**32 or (os_name, arch) != ('windows', 'x64') or args.profile != 'modern'):
            raise ValueError('Local test builds require native Windows x64 and the modern profile')
        if (not re.fullmatch(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)-(alpha|beta|rc)\.(0|[1-9]\d*)', args.version)
                or any(int(part) > 65535 for part in args.version.split('-')[0].split('.'))
                or int(args.version.rsplit('.', 1)[1]) > 9999):
            raise ValueError('Local test builds require a bounded prerelease version')
    target = os_name + '-' + arch + '-' + args.profile
    root = ROOT / 'artifacts' / 'local-test' if local_test else ROOT / 'artifacts'
    return dict(os=os_name, architecture=arch, target=target, ui=ui_for_profile(os_name, arch, args.profile),
                local_test=local_test, output=root / target / args.version,
                metadata={'build_channel': 'local-test'} if local_test else {})


def ui_for_profile(os_name, arch, profile):
    if profile == 'legacy' and os_name == 'windows' and arch in ('x86', 'x64'):
        expected = 'qt'
    elif profile == 'modern' and os_name in ELECTRON_PLATFORMS and arch in ('x64', 'arm64'):
        expected = 'electron'
    else:
        raise ValueError('Unsupported native UI target')
    if os.environ.get('SOFT_TRACKING_UI', expected) != expected:
        raise ValueError('SOFT_TRACKING_UI does not match the runtime profile')
    return expected


def ui_compatibility(os_name, ui):
    if ui == 'qt':
        return {'ui': 'qt', 'profile': 'legacy', 'requires_os_acceptance': True}
    requirements = {
        'windows': {'minimum_os_version': '10.0.18362'},
        'macos': {'minimum_os_version': '13.0'},
        # This is our tested package baseline, not a claim about every Electron distro.
        'linux': {'minimum_glibc': '2.35', 'tested_distribution': 'Ubuntu 22.04',
                  'native_activity_session': 'x11'},
    }
    return dict(requirements[os_name], ui='electron', electron_version=ELECTRON_VERSION,
                requires_compatibility_gate=True)


def file_digest(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def electron_executable(os_name):
    return Path({'windows': 'SoftTrackingUI.exe',
                 'macos': 'SoftTrackingUI.app/Contents/MacOS/SoftTrackingUI',
                 'linux': 'SoftTrackingUI'}[os_name])


def electron_package(os_name, arch, local_test=False):
    desktop = ROOT / 'desktop'
    package = json.loads((desktop / 'package.json').read_text(encoding='utf-8'))
    lock = json.loads((desktop / 'package-lock.json').read_text(encoding='utf-8'))
    if package.get('devDependencies', {}).get('electron') != ELECTRON_VERSION:
        raise ValueError('Desktop must pin electron exactly to ' + ELECTRON_VERSION)
    if lock.get('packages', {}).get('node_modules/electron', {}).get('version') != ELECTRON_VERSION:
        raise ValueError('Desktop Electron lockfile does not match the release contract')
    output = desktop / 'out' / 'local-test' if local_test else desktop / 'out'
    source = output / ('SoftTrackingUI-' + ELECTRON_PLATFORMS[os_name] + '-' + arch)
    if not (source / electron_executable(os_name)).is_file():
        raise ValueError('Missing native Electron package: ' + str(source))
    resources = source / ('SoftTrackingUI.app/Contents/Resources' if os_name == 'macos' else 'resources')
    if not (resources / 'app.asar').is_file():
        raise ValueError('Electron application must be packaged as resources/app.asar')
    if (resources / 'app').exists():
        raise ValueError('Unpacked development app must not be shipped beside app.asar')
    return source


def copy_electron(source, destination):
    source = Path(source).resolve()
    destination = Path(destination)
    directories, files, links, seen = [], [], [], set()
    # Do not traverse framework aliases: materializing them triples Electron's size.
    for directory, names, filenames in os.walk(source, followlinks=False):
        for name in sorted(names + filenames):
            item = Path(directory) / name
            relative = item.relative_to(source)
            if name in ('node_modules', '.git', '.env', MAP_NAME) or item.suffix == '.map':
                raise ValueError('Development files or reserved map found in packaged Electron')
            folded = relative.as_posix().casefold()
            if folded in seen:
                raise ValueError('Case-colliding Electron package paths')
            seen.add(folded)
            mode = item.lstat().st_mode
            if stat.S_ISLNK(mode):
                links.append({'path': relative.as_posix(), 'target': os.readlink(item)})
                if name in names:
                    names.remove(name)
            elif stat.S_ISDIR(mode):
                directories.append(relative)
            elif stat.S_ISREG(mode):
                files.append(relative)
            else:
                raise ValueError('Non-regular Electron package entry')
    document = {'version': 1, 'links': links}
    raw = json.dumps(document, sort_keys=True, separators=(',', ':')).encode('utf-8')
    if len(raw) > MAX_MAP_BYTES:
        raise ValueError('Electron link map is too large')
    plan = plan_links(source, document)
    # The legacy ZIP contract stores files only; it cannot preserve empty targets.
    stored_directories = {parent for file in files for parent in file.parents}
    for parts, _target, canonical in plan:
        if (Path(*parts[:-1]) not in stored_directories
                or ((source.joinpath(*canonical)).is_dir() and Path(*canonical) not in stored_directories)):
            raise ValueError('Electron alias requires a directory absent from the regular-file payload')
    destination.mkdir(parents=True, exist_ok=False)
    for relative in directories:
        (destination / relative).mkdir()
    for relative in files:
        shutil.copy2(source / relative, destination / relative, follow_symlinks=False)
    if links:
        with (destination / MAP_NAME).open('xb') as stream:
            stream.write(raw)


def write_payload_archive(payload, archive):
    payload = Path(payload)
    if not stat.S_ISDIR(payload.lstat().st_mode):
        raise ValueError('Update payload root must be a real directory')
    files = []
    for directory, names, filenames in os.walk(payload, followlinks=False):
        for name in names + filenames:
            file = Path(directory) / name
            mode = file.lstat().st_mode
            if stat.S_ISREG(mode):
                files.append(file)
            elif not stat.S_ISDIR(mode):
                raise ValueError('Update payload must contain only regular files')
    files.sort()
    unpacked = sum(file.stat().st_size for file in files)
    if len(files) > 20000 or unpacked > TRANSITION_UNPACKED_BYTES:
        raise ValueError('Payload exceeds the 3.0.4/3.1.0 extraction limit (' + str(unpacked) +
                         ' bytes, ' + str(len(files)) + ' files); a bridge release is required')
    with zipfile.ZipFile(archive, 'x', zipfile.ZIP_DEFLATED, compresslevel=9) as package:
        for file in files:
            if file.is_symlink() or not stat.S_ISREG(file.stat().st_mode):
                raise ValueError('Update payload must contain only regular files')
            package.write(file, file.relative_to(payload).as_posix())
    if archive.stat().st_size > TRANSITION_ARCHIVE_BYTES:
        raise ValueError('Payload exceeds the 3.0.4/3.1.0 download limit (' + str(archive.stat().st_size) +
                         ' bytes); a bridge release is required')


def keygen(path,key_id):
    from nacl.signing import SigningKey
    from agent_tracker.integration import protect_workspace
    path=Path(path).resolve(); protect_workspace(path.parent)
    private=SigningKey.generate()
    with path.open("xb") as stream:
        stream.write(bytes(private))
    public=base64.b64encode(bytes(private.verify_key)).decode("ascii")
    atomic_json(path.with_suffix(".public.json"),{key_id:public})


def freeze(entry,name,dist,work,data=(),console=False,ui=None):
    ui = ui or os.environ.get('SOFT_TRACKING_UI', 'qt')
    if ui not in ('electron', 'qt'):
        raise ValueError('Unknown desktop UI backend')
    directory = entry == 'runtime_entry.py'
    args=[sys.executable,"-m","PyInstaller","--noconfirm","--onedir" if directory else "--onefile","--noupx","--name",name,"--distpath",str(dist),"--workpath",str(work / name),"--specpath",str(work),"--paths",str(ROOT / "agent")]
    # PyNaCl loads this compiled dependency dynamically; analysis can miss it on ARM.
    args.extend(["--hidden-import","_cffi_backend"])
    if ui == 'electron' or entry not in ('runtime_entry.py', 'setup_entry.py'):
        for module in ('PySide2', 'PySide6', 'PyQt5', 'PyQt6', 'shiboken2', 'shiboken6',
                       'agent_tracker.qt_desktop', 'agent_tracker.ui.quick'):
            args.extend(['--exclude-module', module])
    if ui == 'electron':
        args.extend(['--exclude-module', 'tkinter', '--exclude-module', '_tkinter'])
    if not console and platform.system() in ("Windows","Darwin"):
        args.append("--windowed")
    if os.name=="nt":
        args.extend(["--icon",str(ROOT / "agent/agent_tracker/assets/app.ico")])
    for source,target in data:
        args.extend(["--add-data",str(source)+os.pathsep+target])
    if entry=="runtime_entry.py":
        if ui == 'qt':
            args.extend(['--additional-hooks-dir', str(ROOT/'tools/hooks')])
            from importlib.util import find_spec
            qt = 'PySide6' if find_spec('PySide6') else 'PySide2'
            args.extend(['--hidden-import', qt+'.QtQuickControls2'])
            args.extend(['--add-data', str(ROOT/'agent/agent_tracker/ui/quick')+os.pathsep+'agent_tracker/ui/quick'])
        os_module={"Windows":"windows","Darwin":"macos","Linux":"linux"}[platform.system()]
        args.extend(["--hidden-import","agent_tracker.platform."+os_module])
    args.append(str(ROOT / "agent" / entry))
    subprocess.run(args,check=True)


def build(args):
    plan = build_plan(args)
    import re
    from nacl.signing import SigningKey
    from agent_tracker.core.release_manager import executable_name
    os_name, arch, ui, target = plan['os'], plan['architecture'], plan['ui'], plan['target']
    desktop = electron_package(os_name, arch, local_test=plan['local_test']) if ui == 'electron' else None
    if args.profile=="legacy" and sys.version_info[:2] not in [(3,9),(3,10)]: raise ValueError("Legacy builds require Python 3.9/3.10 and a legacy-compatible OS/dependency environment")
    key=SigningKey(Path(args.key).read_bytes())
    public=base64.b64encode(bytes(key.verify_key)).decode("ascii")
    out=plan['output']
    out.mkdir(parents=True,exist_ok=False)
    work=out / "build"; work.mkdir()
    payload=out / "payload"; (payload / "app").mkdir(parents=True)
    (payload / "launcher").mkdir()
    app_name = Path(executable_name()).stem
    freeze('runtime_entry.py', app_name, out/'runtime-dist', work,
           [(ROOT/'agent/agent_tracker/assets','agent_tracker/assets')], ui=ui)
    shutil.copytree(out/'runtime-dist'/app_name, payload/'app', dirs_exist_ok=True)
    if desktop:
        copy_electron(desktop, payload/'app/electron')
    freeze('native_entry.py', Path(executable_name(True)).stem, payload/'app', work, console=True, ui=ui)
    for native,entry in [(False,"bootstrap_entry.py"),(True,"bootstrap_native_entry.py")]:
        freeze(entry,Path(executable_name(native,True)).stem,payload / "launcher",work,console=native,ui=ui)
    extension=payload / "extension"
    shutil.copytree(ROOT / "extension",extension,ignore=shutil.ignore_patterns("tests","compat","*.md"))
    manifest=json.loads((extension / "manifest.json").read_text(encoding="utf-8"))
    parts=args.version.split('-')
    fourth=60000
    if len(parts)>1:
        stage,number=parts[1].split('.')
        if int(number)>9999:raise ValueError('Prerelease sequence exceeds browser version range')
        fourth={'alpha':10000,'beta':20000,'rc':30000}[stage]+int(number)
    if any(int(part)>65535 for part in parts[0].split('.')):raise ValueError('Browser version component exceeds 65535')
    manifest["version"]=parts[0]+'.'+str(fourth)
    manifest["version_name"]=args.version
    if os_name=="macos" and args.profile=="legacy":
        manifest["manifest_version"]=2
        manifest["permissions"]=[p for p in manifest["permissions"] if p!='scripting']+manifest.pop("host_permissions")
        manifest["browser_action"]=manifest.pop("action")
        manifest["background"]={"scripts":["privacy.js","outbox.js","background.js"],"persistent":False}
    atomic_json(extension / "manifest.json",manifest)
    locale=manifest.get('default_locale')
    if not locale or not (extension / '_locales' / locale / 'messages.json').is_file():
        raise ValueError('Packaged extension has no valid default_locale')
    messages=json.loads((extension / '_locales' / locale / 'messages.json').read_text(encoding='utf-8'))
    for message in re.findall(r'__MSG_(\w+)__',json.dumps(manifest)):
        if message not in messages: raise ValueError('Missing extension localization: '+message)
    metadata={"version":args.version,"os":os_name,"architecture":arch,"target":target,"launcher_protocol":1,"extension_version":manifest["version"],"python":platform.python_version()}
    metadata.update(ui_compatibility(os_name, ui))
    metadata.update(plan['metadata'])
    atomic_json(payload / "build.json",metadata)
    files = [file for file in payload.rglob('*') if file.is_file()]
    sizes = {'target': target, 'version': args.version, 'ui': ui, 'file_count': len(files),
             'payload_unpacked_bytes': sum(file.stat().st_size for file in files),
             'electron_unpacked_bytes': sum(file.stat().st_size for file in (payload/'app/electron').rglob('*') if file.is_file()),
             'archive_limit_bytes': TRANSITION_ARCHIVE_BYTES, 'unpacked_limit_bytes': TRANSITION_UNPACKED_BYTES}
    atomic_json(out/'size-report.json', sizes)
    archive=out / ('release-'+args.version+'.zip')
    try:
        write_payload_archive(payload, archive)
    finally:
        if archive.exists():
            sizes['archive_bytes'] = archive.stat().st_size
            atomic_json(out/'size-report.json', sizes)
    release=dict(metadata,protocol=3,expires_at=int(time.time())+90*86400,size=archive.stat().st_size,sha256=file_digest(archive),url='https://tracking.salesoftech.com/client/v3/releases/'+target+'/'+archive.name)
    raw=json.dumps(release,sort_keys=True,separators=(',',':')).encode()
    envelope={"key_id":args.key_id,"payload":base64.b64encode(raw).decode(),"signature":base64.b64encode(key.sign(raw).signature).decode()}
    atomic_json(out / "manifest.json",envelope)
    setup=out / "setup-payload"; setup.mkdir()
    if desktop:
        shutil.copytree(payload/'app/electron', setup/'electron')
    guide = setup / "guide"
    guide.mkdir()
    for name in ("setup.html", "setup.js", "setup.css", "locale.js"):
        shutil.copy2(extension / name, guide / name)
    shutil.copytree(extension / "icons", guide / "icons")
    shutil.copy2(archive,setup / "release.zip")
    atomic_json(setup / "manifest.json",envelope)
    atomic_json(setup / "trusted-update-keys.json",{args.key_id:public})
    atomic_json(setup / "setup-build.json",metadata)
    freeze("setup_entry.py","SOFT-Tracking-Setup",out,work,[(setup,"setup-payload")],ui=ui)
    if os_name=="macos":
        installer=out / "SOFT-Tracking-Setup.dmg"
        subprocess.run(["hdiutil","create","-volname","SOFT Tracking Setup","-srcfolder",str(out / "SOFT-Tracking-Setup.app"),"-ov",str(installer)],check=True)
    elif os_name=="windows": installer=out / "SOFT-Tracking-Setup.exe"
    else:
        installer=out / "SOFT-Tracking-Setup.run"
        (out / "SOFT-Tracking-Setup").rename(installer)
    setup_metadata = dict(ui_compatibility(os_name, ui), file=installer.name, sha256=file_digest(installer), version=args.version, target=target)
    setup_metadata.update(plan['metadata'])
    atomic_json(out / "setup.json", setup_metadata)
    sizes['installer_bytes'] = installer.stat().st_size
    sizes['setup_payload_unpacked_bytes'] = sum(file.stat().st_size for file in setup.rglob('*') if file.is_file())
    atomic_json(out/'size-report.json', sizes)
    with (out/'dependencies.txt').open('w',encoding='utf-8') as dependencies:
        subprocess.run([sys.executable,'-m','pip','freeze'],stdout=dependencies,check=True)
    if desktop:
        shutil.copy2(ROOT/'desktop/package-lock.json', out/'desktop-package-lock.json')
    print('Build complete: '+str(installer))
    print('Not published. Test installation/update/rollback before promoting these artifacts.')


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    sub=parser.add_subparsers(dest='command',required=True)
    generate=sub.add_parser('keygen'); generate.add_argument('--key',required=True); generate.add_argument('--key-id',required=True)
    compile_=sub.add_parser('build'); compile_.add_argument('--key',required=True); compile_.add_argument('--key-id',required=True); compile_.add_argument('--version',required=True); compile_.add_argument('--profile',choices=['modern','legacy'],default='modern')
    compile_.add_argument('--local-test', action='store_true', help='Explicit Windows x64 modern prerelease test build; never a publish candidate')
    args=parser.parse_args()
    keygen(args.key,args.key_id) if args.command=='keygen' else build(args)
