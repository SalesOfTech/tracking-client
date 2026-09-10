import os
import subprocess
import psutil
import json
from pathlib import Path

from release import electron_executable, file_digest


def assert_ui_payload(app, metadata, setup=None):
    app = Path(app)
    electron = app / 'electron'
    if metadata.get('ui') != 'electron':
        assert not electron.exists(), 'Legacy payload unexpectedly contains Electron'
        return
    assert (electron / electron_executable(metadata['os'])).is_file(), 'Missing packaged Electron executable'
    assert metadata.get('electron_version') == '44.3.0', 'Wrong packaged Electron version'
    for file in app.rglob('*'):
        assert not any(name in file.name.lower() for name in ('pyside', 'pyqt', 'shiboken')), 'Qt leaked into the Electron payload'
    if setup:
        expected = {file.relative_to(electron): file_digest(file) for file in electron.rglob('*') if file.is_file()}
        installed = Path(setup) / 'electron'
        actual = {file.relative_to(installed): file_digest(file) for file in installed.rglob('*') if file.is_file()}
        assert expected == actual, 'Installer and runtime do not contain the same complete Electron package'


def assert_ui_health(path, metadata):
    result = json.loads(Path(path).read_text(encoding='utf-8'))
    if metadata.get('ui') == 'electron':
        assert result.get('ui') == 'electron' and result.get('ready') is True, 'Electron renderer/RPC health was not confirmed'


def child_processes(process):
    return psutil.Process(process.pid).children(recursive=True)


def assert_electron_child(children, app, metadata):
    if metadata.get('ui') != 'electron':
        return
    expected = (Path(app) / 'electron' / electron_executable(metadata['os'])).resolve()
    for child in children:
        try:
            if child.is_running() and Path(child.exe()).resolve() == expected:
                return
        except psutil.NoSuchProcess:
            continue
    raise AssertionError('Ready marker was written without a running packaged Electron child')


def assert_children_stopped(children):
    _, alive = psutil.wait_procs(children, timeout=10)
    if alive:
        # Clean up only processes captured from this isolated CI installation.
        for child in alive:
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        psutil.wait_procs(alive, timeout=10)
        raise AssertionError('Graceful shutdown left child processes running')


def stop_tree(process):
    try:
        parent=psutil.Process(process.pid)
        children=parent.children(recursive=True)
    except psutil.NoSuchProcess:
        return
    processes=children+[parent]
    for child in reversed(processes):
        try:child.terminate()
        except psutil.NoSuchProcess:pass
    _,alive=psutil.wait_procs(processes,timeout=10)
    for child in alive:
        try:child.kill()
        except psutil.NoSuchProcess:pass
    psutil.wait_procs(alive,timeout=10)


def run_frozen(args,env,timeout,input=None):
    process=subprocess.Popen(args,env=env,stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
                             stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    try:
        output,error=process.communicate(input,timeout=timeout)
        if process.returncode:
            diagnostics(env)
        return subprocess.CompletedProcess(args,process.returncode,output,error)
    except subprocess.TimeoutExpired:
        diagnostics(env)
        stop_tree(process)
        raise


def diagnostics(env):
    if env.get('SOFT_TRACKING_HEALTH'):
        for suffix in ('.phase','.error'):
            path=Path(env['SOFT_TRACKING_HEALTH']+suffix)
            if path.is_file():print(path.read_text(encoding='utf-8')[:8000],flush=True)
