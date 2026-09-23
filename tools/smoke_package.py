"""Exercise installed, frozen GUI and native binaries without browser/OS registration."""
import argparse
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'agent'))
from agent_tracker.installer import install
from agent_tracker.core.release_manager import executable_name,release_path
from agent_tracker.native_host import ALLOWED_ORIGIN
from agent_tracker.core.files import atomic_json
from smoke_helpers import (run_frozen, stop_tree, assert_ui_payload, assert_ui_health,
                           child_processes, assert_electron_child, assert_children_stopped)


def main(bundle):
    bundle = Path(bundle)
    metadata = json.loads((bundle/'setup-build.json').read_text(encoding='utf-8'))
    # Older baseline packages legitimately predate the standalone migration tool.
    migration_descriptor = bundle.parent / 'migration.json'
    if metadata['os'] == 'windows' and migration_descriptor.is_file():
        migration = json.loads(migration_descriptor.read_text(encoding='utf-8'))
        if migration.get('file') != 'SOFT-Tracking-Migrate.exe':
            raise RuntimeError('Unexpected migration smoke binary')
        result = run_frozen([str(bundle.parent / migration['file']), '--self-test'], timeout=180)
        if result.returncode or b'PASS: isolated frozen migration smoke' not in result.stdout:
            raise RuntimeError('Frozen migration smoke failed (no real user migration was requested)')
    for base in (Path(bundle) / 'guide',):
        for name in ('setup.html', 'setup.js', 'setup.css', 'locale.js', 'icons/icon48.png'):
            assert (base / name).is_file(), 'Missing offline setup guide: ' + name
    with tempfile.TemporaryDirectory(prefix='soft-tracking-package-') as temporary:
        root=Path(temporary)/'install'
        install(Path(bundle),root,'a'*32,integrate=False,language='cs')
        for name in ('setup.html', 'setup.js', 'setup.css', 'locale.js'):
            assert (root / 'extension' / name).is_file(), 'Missing installed guide: ' + name
        health=Path(temporary)/'health.json'
        env=dict(os.environ,SOFT_TRACKING_INSTALL=str(root),SOFT_TRACKING_HEALTH=str(health),LOCALAPPDATA=temporary,XDG_DATA_HOME=temporary)
        version=json.loads((root/'current.json').read_text())['version']
        app=release_path(root,version)/'app'/executable_name()
        assert_ui_payload(app.parent, metadata, bundle)
        result=run_frozen([str(app),'--health-check'],env=env,timeout=60)
        if result.returncode!=0 or not health.exists():
            raise RuntimeError('Frozen application health check failed: '+result.stderr.decode(errors='replace'))
        assert_ui_health(health, metadata)
        if metadata.get('ui') == 'electron':
            installer = bundle.parent / {'windows': 'SOFT-Tracking-Setup.exe',
                                         'macos': 'SOFT-Tracking-Setup.app/Contents/MacOS/SOFT-Tracking-Setup',
                                         'linux': 'SOFT-Tracking-Setup.run'}[metadata['os']]
            setup_health = Path(temporary)/'setup-health.json'
            before = (root/'current.json').read_bytes()
            result = run_frozen([str(installer), '--health-check'],
                                env=dict(env, SOFT_TRACKING_HEALTH=str(setup_health)), timeout=120)
            if result.returncode != 0 or not setup_health.exists():
                raise RuntimeError(
                    'Frozen installer Electron renderer health check failed '
                    '(exit {}): {}'.format(result.returncode,
                                          result.stderr.decode(errors='replace')[-8000:]))
            assert_ui_health(setup_health, metadata)
            assert (root/'current.json').read_bytes() == before, 'Installer health check changed the installation'
        payload=b'{"action":"status"}'
        host=root/executable_name(True,True)
        reply=run_frozen([str(host),ALLOWED_ORIGIN],input=struct.pack('=I',len(payload))+payload,env=env,timeout=45)
        if reply.returncode or len(reply.stdout)<4:raise RuntimeError('Frozen native host failed: '+reply.stderr.decode(errors='replace'))
        size=struct.unpack('=I',reply.stdout[:4])[0]
        value=json.loads(reply.stdout[4:4+size])
        assert value['ok'] and value['status']['identity'] is None
        assert value['status']['language']=='cs', 'Installer language was not preserved in frozen native host'
        assert value['status']['version']==version, 'Frozen client reports the wrong application version'
        assert 'device_secret' not in json.dumps(value)
        browser_payload=json.dumps({'action':'status','browser':{'profile':'b'*32,'family':'Chrome','version':json.loads((root/'extension/manifest.json').read_text())['version']}}).encode()
        reply=run_frozen([str(host),ALLOWED_ORIGIN],input=struct.pack('=I',len(browser_payload))+browser_payload,env=env,timeout=45)
        assert reply.returncode==0 and json.loads(reply.stdout[4:])['ok'], 'Frozen browser health receipt failed'
        launcher=subprocess.Popen([str(root/executable_name(False,True)),'--autostart'],env=env,
                                  creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        try:
            deadline=time.monotonic()+45
            ready=[]
            while time.monotonic()<deadline and launcher.poll() is None:
                ready=list((root/'run').glob('*.ready'))
                if ready:break
                time.sleep(0.2)
            assert ready and launcher.poll() is None,'Installed launcher did not start the desktop'
            children = child_processes(launcher)
            assert_electron_child(children, app.parent, metadata)
            enrollment=(root/'enrollment.json').read_bytes()
            reopened=install(Path(bundle),root,'b'*32,integrate=False,company_resolver=lambda code:32)
            assert reopened.samefile(root/executable_name(False,True)) and launcher.poll() is None
            assert (root/'enrollment.json').read_bytes()==enrollment,'Repeated download changed enrollment'
            atomic_json(root/'stop-request.json',{'token':ready[0].stem})
            assert launcher.wait(timeout=45)==0,'Installed application did not stop gracefully'
            assert_children_stopped(children)
        finally:
            if launcher.poll() is None:
                stop_tree(launcher)
                launcher.wait(timeout=10)
        print('PASS: signed package install, frozen '+metadata.get('ui', 'qt')+'/native health checks, installed launcher/supervisor/desktop startup and graceful shutdown, isolated local state')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('bundle');args=parser.parse_args();main(args.bundle)
