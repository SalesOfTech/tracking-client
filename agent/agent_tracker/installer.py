from __future__ import annotations
import json
import os
import platform
import plistlib
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from contextlib import closing, contextmanager
from pathlib import Path
from .browser_setup import register_host
from .core.client import workspace, company_code_from_filename
from .core.networking import HttpClient
from .core.files import atomic_json, read_json
from .core.instance import SingleInstance
from .core.release_manager import executable_name, ReleaseManager, release_path
from .core.signed_updates import verify_manifest, stage_archive
from .integration import protect_workspace, autostart
from .core.target import runtime_target
from .i18n import LANGUAGES, detect_language, translate


def bootstrap_code(executable):
    code = company_code_from_filename(str(executable))
    if code or sys.platform != "darwin":
        return code
    # The company code is on the downloaded DMG, not the .app inside it.
    data = subprocess.run(["hdiutil","info","-plist"],capture_output=True,check=True).stdout
    for image in plistlib.loads(data).get("images",[]):
        for entity in image.get("system-entities",[]):
            mount = entity.get("mount-point")
            if mount and Path(mount).resolve() in Path(executable).resolve().parents:
                return company_code_from_filename(image.get("image-path",""))
    return ""


class InstallerError(ValueError):
    pass


def resolve_company(code):
    try:
        http = HttpClient('https://tracking.salesoftech.com')
        try:
            result = http.post_json('/client/v3/installation', {'company_code': code})
        finally:
            http.session.close()
        company = result.get('company_id')
        if result.get('ok') is not True or type(company) is not int or company <= 0:
            raise ValueError('Invalid company response')
        return company
    except Exception as error:
        raise InstallerError('setup_company_unavailable') from error


def check_existing_company(root, existing, company_code, resolver):
    old_code = existing.get('company_code')
    if old_code == company_code:
        return
    company = None
    database = root.parent / 'state.sqlite3'
    if database.exists():
        try:
            with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as connection:
                row = connection.execute("SELECT value FROM state WHERE name='identity'").fetchone()
                identity = json.loads(row[0]) if row else {}
                company = identity.get('company_id')
        except (sqlite3.Error, ValueError, AttributeError) as error:
            raise InstallerError('setup_existing_damaged') from error
    if not old_code and company is None:
        return
    if type(company) is not int or company <= 0:
        company = resolver(old_code)
    if resolver(company_code) != company:
        raise InstallerError('setup_company_conflict')


def installed_launcher(root):
    from .bootstrap import app_path
    launcher = root / executable_name(False, True)
    try:
        if not launcher.is_file() or not app_path(root).is_file():
            raise ValueError('Missing installed application')
    except (ValueError, OSError) as error:
        raise InstallerError('setup_existing_damaged') from error
    return launcher


@contextmanager
def stopped_supervisor(root, timeout=65):
    lock=SingleInstance(root / 'supervisor.lock')
    deadline=time.monotonic()+timeout
    requested=None
    try:
        while True:
            try:
                lock.__enter__()
                break
            except RuntimeError:
                if time.monotonic()>=deadline:
                    raise InstallerError('setup_close_required')
                ready=[path for path in (root/'run').glob('*.ready') if re.fullmatch('[a-f0-9]{32}',path.stem)]
                if ready:
                    requested=max(ready,key=lambda path:path.stat().st_mtime).stem
                    atomic_json(root/'stop-request.json',{'token':requested})
                time.sleep(0.3)
        try:
            yield
        finally:
            lock.__exit__(None,None,None)
    finally:
        if requested and read_json(root/'stop-request.json',{}).get('token')==requested:
            (root/'stop-request.json').unlink(missing_ok=True)


def installed_health(root, version):
    health=root/('installer-health-'+uuid.uuid4().hex+'.json')
    env=dict(os.environ,SOFT_TRACKING_INSTALL=str(root),SOFT_TRACKING_HEALTH=str(health))
    try:
        result=subprocess.run([str(release_path(root,version)/'app'/executable_name()),'--health-check'],
                              env=env,capture_output=True,timeout=65,
                              creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        if result.returncode or not health.is_file():
            raise InstallerError('setup_upgrade_failed')
    finally:
        health.unlink(missing_ok=True)
        Path(str(health)+'.phase').unlink(missing_ok=True)


def upgrade_existing(bundle,root,language='',integrate=True,health_check=None):
    from packaging.version import Version
    launcher=installed_launcher(root)
    manager=ReleaseManager(root)
    try:
        with SingleInstance(root/'install.lock'):
            active=manager.active()
            build=read_json(release_path(root,active['version'])/'build.json')
            manifest=verify_manifest(read_json(bundle/'manifest.json'),manager.keys,'0.0.0',build['os'],build['architecture'])
            if manifest['target']!=build['target']:
                raise InstallerError('setup_wrong_target')
            if Version(manifest['version'])<=Version(active['version']):
                return launcher
            staged=manager.stage(bundle/'release.zip',manifest)
            with stopped_supervisor(root):
                # A running updater may have completed while the installer was staging.
                if Version(manager.active()['version'])>=Version(manifest['version']):
                    return launcher
                manager.activate(staged)
                try:
                    (health_check or installed_health)(root,manifest['version'])
                    manager.confirm()
                except Exception as error:
                    manager.rollback()
                    raise InstallerError('setup_upgrade_failed') from error
                if language in LANGUAGES:
                    profile=read_json(root/'enrollment.json',{})
                    profile.update(language=language,language_source='manual')
                    atomic_json(root/'enrollment.json',profile)
                atomic_json(root/'update-status.json',{'state':'installed','version':manifest['version']})
        return launcher
    except InstallerError as error:
        if str(error)=='setup_upgrade_failed' and integrate:
            subprocess.Popen([str(launcher)])
        raise
    finally:
        manager.session.close()


def install(bundle, root, company_code, integrate=True, language="", company_resolver=None, health_check=None):
    bundle, root = Path(bundle).resolve(), Path(root).resolve()
    if not re.fullmatch(r"[a-f0-9]{32}",company_code):
        raise InstallerError('setup_code_required')
    resolver = company_resolver or resolve_company
    # An already-running agent can be reopened without taking its supervisor lock.
    if (root / 'current.json').exists():
        check_existing_company(root, read_json(root / 'enrollment.json', {}), company_code, resolver)
        return upgrade_existing(bundle,root,language,integrate,health_check)
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    with SingleInstance(root / "supervisor.lock"), SingleInstance(root / "install.lock"):
        existing=read_json(root / "enrollment.json",{})
        check_existing_company(root, existing, company_code, resolver)
        if (root / "current.json").exists():
            return installed_launcher(root)
        build=read_json(bundle / "setup-build.json")
        target_os,arch=runtime_target()
        envelope=read_json(bundle / "manifest.json")
        keys=read_json(bundle / "trusted-update-keys.json")
        release=verify_manifest(envelope,keys,"0.0.0",target_os,arch)
        if release.get("target")!=build["target"]:
            raise ValueError("Wrong installation target")
        destination=root / "versions" / release["version"]
        stage_archive(bundle / "release.zip",release,destination)
        installed_build=read_json(destination / "build.json",{})
        if installed_build.get("version")!=release["version"] or installed_build.get("target")!=build["target"] or installed_build.get("launcher_protocol")!=1:
            raise ValueError("Incompatible installation layout")
        for native in (False,True):
            filename=executable_name(native,bootstrap=True)
            shutil.copy2(destination / "launcher" / filename,root / filename)
        shutil.copytree(destination / "extension",root / "extension")
        atomic_json(root / "trusted-update-keys.json",keys)
        enrollment = {"company_code": company_code}
        if language in LANGUAGES:
            enrollment["language"] = language
            enrollment["language_source"] = "manual"
        atomic_json(root / "enrollment.json", enrollment)
        atomic_json(root / "current.json",{"version":release["version"]})
        if integrate:
            protect_workspace(root.parent)
            register_host(root / executable_name(True,True),root.parent)
            autostart(root / executable_name(False,True),True)
            shortcuts(root)
    return root / executable_name(False,True)


def shortcuts(root):
    launcher=root / executable_name(False,True)
    if os.name=="nt":
        from win32com.client import Dispatch
        folder=Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        link=Dispatch("WScript.Shell").CreateShortCut(str(folder / "SOFT Tracking.lnk"))
        link.Targetpath=str(launcher); link.WorkingDirectory=str(root); link.save()
    elif sys.platform=="darwin":
        app=Path.home() / "Applications" / "SOFT Tracking.app" / "Contents"
        (app / "MacOS").mkdir(parents=True,exist_ok=True)
        # A tiny application wrapper keeps the actual versioned installation permanent.
        script=app / "MacOS" / "tracking"
        import shlex
        script.write_text('#!/bin/sh\nexec '+shlex.quote(str(launcher))+' "$@"\n',encoding="utf-8")
        script.chmod(0o700)
        with (app / "Info.plist").open("wb") as stream:
            plistlib.dump({"CFBundleName":"SOFT Tracking","CFBundleIdentifier":"com.soft.tracking","CFBundleExecutable":"tracking","CFBundlePackageType":"APPL"},stream)
    else:
        folder=Path(os.environ.get("XDG_DATA_HOME",Path.home()/".local"/"share")) / "applications"
        folder.mkdir(parents=True,exist_ok=True)
        quoted=str(launcher).replace('\\','\\\\').replace('"','\\"').replace('$','\\$').replace('`','\\`')
        (folder / "soft-tracking-v3.desktop").write_text('[Desktop Entry]\nType=Application\nName=SOFT Tracking\nExec="'+quoted+'"\nTerminal=false\n',encoding="utf-8")


class InstallerWindow:
    def __init__(self, bundle, company_code="", language=""):
        import tkinter as tk
        from tkinter import ttk
        from .ui.theme import apply_theme
        self.bundle = Path(bundle)
        self.language_override = language if language in LANGUAGES else ""
        self.language = detect_language(language)
        self.thread = None
        self.outcome = {}
        self.translations = []
        self.root = tk.Tk()
        apply_theme(self.root)
        self.root.geometry("720x440")
        self.root.minsize(680, 440)
        frame = ttk.Frame(self.root, padding=28)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        ttk.Label(frame, text="SOFT Tracking", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        self.selector = ttk.Combobox(frame, values=list(LANGUAGES.values()), width=13, state="readonly")
        self.selector.set(LANGUAGES[self.language])
        self.selector.grid(row=0, column=1, sticky="e", padx=(16, 0))
        self.selector.bind("<<ComboboxSelected>>", self.change_language)
        def label(key, **options):
            widget = ttk.Label(frame, **options)
            self.translations.append((widget, key))
            return widget
        label("setup_subtitle", style="Muted.TLabel").grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 24))
        label("company_code").grid(row=2, column=0, columnspan=2, sticky="w")
        self.code = ttk.Entry(frame)
        self.code.insert(0, company_code)
        self.code.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 12))
        label("setup_note", style="Muted.TLabel", wraplength=620).grid(row=4, column=0, columnspan=2, sticky="w")
        self.status_key = ""
        self.status = tk.StringVar()
        ttk.Label(frame, textvariable=self.status, wraplength=620).grid(row=5, column=0, columnspan=2, sticky="w", pady=16)
        self.progress = ttk.Progressbar(frame, mode="indeterminate")
        self.progress.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        self.progress.grid_remove()
        self.details = tk.StringVar()
        self.details_key = ""
        ttk.Label(frame, textvariable=self.details, wraplength=620, style="Muted.TLabel").grid(row=7, column=0, columnspan=2, sticky="w")
        frame.rowconfigure(8, weight=1)
        self.guide_button = ttk.Button(frame, command=self.guide)
        self.translations.append((self.guide_button, "guide"))
        self.guide_button.grid(row=9, column=0, sticky="w", pady=(18, 0))
        self.button = ttk.Button(frame, style="Primary.TButton", command=self.start)
        self.translations.append((self.button, "install"))
        self.button.grid(row=9, column=1, sticky="e", pady=(18, 0))
        self.render_language()
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def t(self, key):
        return translate(self.language, key)

    def render_language(self):
        self.root.title(self.t("setup_title"))
        for widget, key in self.translations:
            widget.configure(text=self.t(key))
        self.status.set(self.t(self.status_key) if self.status_key else "")
        if self.details_key:
            self.details.set(self.t(self.details_key))

    def change_language(self, _event=None):
        self.language = next(key for key, name in LANGUAGES.items() if name == self.selector.get())
        self.language_override = self.language
        self.render_language()

    def guide(self):
        try:
            path = (self.bundle / "guide" / "setup.html").resolve(strict=True)
            if not webbrowser.open(path.as_uri() + "#lang=" + self.language):
                raise OSError("No browser")
        except (OSError, webbrowser.Error):
            from tkinter import messagebox
            messagebox.showerror("SOFT Tracking", self.t("package_missing"))

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        value, language = self.code.get().strip(), self.language_override
        for control in (self.button, self.code, self.selector):
            control.state(["disabled"])
        self.status_key = "installing"
        self.details_key = ""
        self.details.set("")
        self.render_language()
        self.progress.grid()
        self.progress.start()
        def task():
            try:
                self.outcome["launcher"] = install(self.bundle, workspace() / "install", value, language=language)
            except InstallerError as error:
                self.outcome["error_key"] = str(error)
            except Exception as error:
                self.outcome["error"] = str(error)
        self.thread = threading.Thread(target=task)
        self.thread.start()
        self.root.after(100, self.poll)

    def poll(self):
        if self.thread.is_alive():
            self.root.after(100, self.poll)
            return
        self.progress.stop()
        self.progress.grid_remove()
        if "error" in self.outcome or "error_key" in self.outcome:
            self.status_key = "setup_failed"
            self.details_key = self.outcome.pop("error_key", "")
            self.details.set(self.outcome.pop("error", ""))
            for control in (self.button, self.code, self.selector):
                control.state(["!disabled"])
            self.render_language()
            return
        self.status_key = "setup_complete"
        self.render_language()
        try:
            subprocess.Popen([str(self.outcome["launcher"])])
        except OSError:
            self.status_key = "launch_failed"
            self.render_language()
            return
        self.root.destroy()

    def close(self):
        if not self.thread or not self.thread.is_alive():
            self.root.destroy()


def main():
    bundle = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "setup-payload"
    try:
        code = bootstrap_code(sys.executable)
    except Exception:
        code = ""
    InstallerWindow(bundle, code).root.mainloop()
    return 0
