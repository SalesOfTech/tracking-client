"""Install two real local builds, exercise update activation and startup rollback."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
CLIENT_ROOT=Path(os.environ.get('TRACKING_SMOKE_CLIENT_ROOT', ROOT)).resolve()
sys.path.insert(0,str(CLIENT_ROOT/'agent'))
import agent_tracker
if Path(agent_tracker.__file__).resolve() != CLIENT_ROOT/'agent/agent_tracker/__init__.py':
    raise RuntimeError('Smoke test imported another client checkout')
from agent_tracker.installer import install
from agent_tracker.core.files import read_json,atomic_json
from agent_tracker.core.release_manager import ReleaseManager,release_path,executable_name
from agent_tracker.core.signed_updates import verify_manifest
from smoke_helpers import (run_frozen, stop_tree, assert_ui_payload, assert_ui_health,
                           child_processes, assert_electron_child, assert_children_stopped)
from release import file_digest
from agent_tracker.core.event_queue import EventQueue
from agent_tracker.core.client import ClientState

if any(not Path(module.__file__).resolve().is_relative_to(CLIENT_ROOT/'agent')
       for name, module in tuple(sys.modules.items())
       if name.startswith('agent_tracker.') and getattr(module, '__file__', None)):
    raise RuntimeError('Smoke test mixed client checkout imports')


def run_installed(root, env, upgrade=None):
    previous = set((root / 'run').glob('*.ready')) if (root / 'run').exists() else set()
    process = subprocess.Popen([str(root / executable_name(False, True)), '--autostart'], env=env,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    try:
        deadline = time.monotonic() + 50
        ready = []
        while time.monotonic() < deadline and process.poll() is None:
            ready = list(set((root / 'run').glob('*.ready')) - previous)
            if ready:
                break
            time.sleep(0.2)
        assert ready and process.poll() is None, 'Original installed launcher did not start the application'
        current = read_json(root/'current.json')['version']
        payload = release_path(root, current)
        children = child_processes(process)
        assert_electron_child(children, payload/'app', read_json(payload/'build.json'))
        if upgrade:
            upgrade()
        else:
            atomic_json(root / 'stop-request.json', {'token': ready[0].stem})
        assert process.wait(timeout=45) == 0, 'Application did not shut down gracefully'
        assert_children_stopped(children)
    finally:
        if process.poll() is None:
            stop_tree(process)
            process.wait(timeout=10)


def main(old,new, published=False):
    with tempfile.TemporaryDirectory(prefix='soft-tracking-update-') as temporary:
        root=Path(temporary)/'install'
        install(Path(old)/'setup-payload',root,'a'*32,integrate=False)
        bootstraps = {executable_name(native, True): file_digest(root/executable_name(native, True)) for native in (False, True)}
        queue=EventQueue(root.parent/'outbox.sqlite3')
        event={'event_id':'e'*32,'type':'navigation','timestamp':1700000000,'url':'https://example.test/'}
        rejected = dict(event, event_id='d'*32)
        confirmed = dict(event, event_id='c'*32)
        queue.push_payload(event)
        queue.push_payload(rejected); queue.mark_rejected(rejected['event_id'], 'policy_disabled')
        queue.push_payload(confirmed); queue.acknowledge([confirmed['event_id']])
        queue.close()
        state=ClientState(root.parent);device=state.get('device');state.close()
        manager=ReleaseManager(root)
        before=manager.active()['version']
        env=dict(os.environ, SOFT_TRACKING_INSTALL=str(root), LOCALAPPDATA=temporary, XDG_DATA_HOME=temporary)
        manual_restart=False
        if published:
            try:
                run_installed(root, env)
            except subprocess.TimeoutExpired:
                # This published beta can hang inside its old Windows ARM tray shutdown.
                # run_installed cleans up only this isolated CI process tree. Production
                # installers time out with setup_close_required and never force-kill it.
                if before!='3.0.0-beta.7' or read_json(release_path(root,before)/'build.json')['target']!='windows-arm64-modern':
                    raise
                manual_restart=True
                assert manager.active()['version']==before
                print('COMPATIBILITY: Windows ARM beta.7 requires closing/restarting the old client before installation',flush=True)
        metadata=read_json(Path(new)/'setup-payload/setup-build.json')
        manifest=verify_manifest(read_json(Path(new)/'manifest.json'),manager.keys,before,metadata['os'],metadata['architecture'])
        staged=manager.stage(Path(new)/('release-'+metadata['version']+'.zip'),manifest)
        manager.activate(staged)
        health=Path(temporary)/'health.json'
        env=dict(os.environ,SOFT_TRACKING_INSTALL=str(root),SOFT_TRACKING_HEALTH=str(health),LOCALAPPDATA=temporary,XDG_DATA_HOME=temporary)
        executable=release_path(root,metadata['version'])/'app'/executable_name()
        assert_ui_payload(executable.parent, metadata)
        result=run_frozen([str(executable),'--health-check'],env=env,timeout=60)
        assert result.returncode==0 and health.exists(),'New frozen version failed its health check'
        assert_ui_health(health, metadata)
        assert manager.active()['version']==metadata['version']
        assert read_json(root/'extension/manifest.json')['version']==metadata['extension_version']
        # Simulate a supervisor crash before confirmation, then restart recovery.
        ReleaseManager(root).rollback()
        assert manager.active()['version']==before
        assert read_json(root/'enrollment.json')['company_code']=='a'*32
        queue=EventQueue(root.parent/'outbox.sqlite3')
        assert queue.batch()==[event] and queue.counts()['pending']==1
        assert queue.counts()['rejected']==1 and queue.rejections([rejected['event_id']])=={rejected['event_id']:'policy_disabled'}
        assert queue.confirmed([event['event_id'], confirmed['event_id']])==[confirmed['event_id']], 'Pending custody became a server ACK'
        queue.close()
        state=ClientState(root.parent);assert state.get('device')==device;state.close()
        if published:
            # The new installer must replace a running, unregistered old client offline.
            if manual_restart:
                install(Path(new)/'setup-payload', root, 'a'*32, integrate=False)
            else:
                run_installed(root, env, lambda: install(Path(new)/'setup-payload', root, 'a'*32, integrate=False))
            run_installed(root, env)
            assert manager.active()['version']==metadata['version']
            queue=EventQueue(root.parent/'outbox.sqlite3')
            assert queue.batch()==[event] and queue.counts()['pending']==1
            assert queue.counts()['rejected']==1 and queue.confirmed([event['event_id'],confirmed['event_id']])==[confirmed['event_id']]
            queue.close()
            state=ClientState(root.parent);assert state.get('device')==device;state.close()
            print('PASS: published '+before+' -> '+metadata['version']+', '+('installer after manual restart' if manual_restart else 'installer upgrades running unregistered client')+', original bootstrap, rollback and pending data retained')
        assert all(file_digest(root/name)==digest for name,digest in bootstraps.items()), 'Upgrade replaced the original bootstrap'
        print('PASS: real signed update, new frozen GUI/native health check, extension switch, interrupted-update rollback, enrollment/data retained')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('old');parser.add_argument('new');parser.add_argument('--published',action='store_true');args=parser.parse_args();main(args.old,args.new,args.published)
