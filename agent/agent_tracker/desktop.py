from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import messagebox, ttk

from .browser_setup import register_host
from .core.client import Client, company_code_from_filename, workspace
from .core.files import atomic_json, read_json
from .core.instance import SingleInstance
from .i18n import LANGUAGES, client_language, translate
from .ui.theme import apply_theme
from .ui.clipboard import attach, paste
from .browser_health import connections, registration


from .runtime import Worker


def open_path(path):
    path = Path(path).resolve(strict=True)
    if os.name == "nt":
        os.startfile(str(path))
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])


def extension_folder():
    if os.environ.get("SOFT_TRACKING_INSTALL"):
        return Path(os.environ["SOFT_TRACKING_INSTALL"]) / "extension"
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "extension"
    return Path(__file__).resolve().parents[2] / "extension"


class Desktop:
    def __init__(self, client, company_code=""):
        self.client = client
        self.enrollment_thread = None
        self.tray = None
        self.closing = False
        self.translations = []
        self.input_error = ''
        self.browser_check = ''
        self.browser_check_thread = None
        self.install = Path(os.environ["SOFT_TRACKING_INSTALL"]) if os.environ.get("SOFT_TRACKING_INSTALL") else None
        enrollment = read_json(self.install / "enrollment.json", {}) if self.install else {}
        self.language = client_language(client.state.get("language", ""), enrollment)
        self.root = tk.Tk()
        self.root.title("SOFT Tracking")
        self.root.geometry("940x650")
        self.root.minsize(840, 600)
        apply_theme(self.root)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        frame = ttk.Frame(self.root)
        frame.grid(sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(2, weight=1)
        header = ttk.Frame(frame, padding=(24, 20))
        header.grid(sticky="ew")
        header.columnconfigure(1, weight=1)
        try:
            from PIL import Image, ImageTk
            self.logo = ImageTk.PhotoImage(Image.open(Path(__file__).parent / "assets" / "tray_light.png").resize((36, 36)))
            ttk.Label(header, image=self.logo).grid(row=0, column=0, rowspan=2, padx=(0, 14))
            self.root.iconphoto(True, self.logo)
        except (ImportError, OSError, tk.TclError):
            pass
        ttk.Label(header, text="SOFT Tracking", style="Title.TLabel").grid(row=0, column=1, sticky="w")
        self.identity = tk.StringVar()
        ttk.Label(header, textvariable=self.identity, style="Muted.TLabel", wraplength=480).grid(row=1, column=1, sticky="w", pady=(6, 0))
        self.label(header, "language", style="Muted.TLabel").grid(row=0, column=2, sticky="w")
        self.language_select = ttk.Combobox(header, values=list(LANGUAGES.values()), state="readonly", width=13)
        self.language_select.set(LANGUAGES[self.language])
        self.language_select.grid(row=1, column=2, sticky="e", padx=(16, 0))
        self.language_select.bind("<<ComboboxSelected>>", self.change_language)
        ttk.Separator(frame).grid(sticky="ew")
        workspace_frame=ttk.Frame(frame)
        workspace_frame.grid(sticky='nsew')
        workspace_frame.columnconfigure(1,weight=1)
        workspace_frame.rowconfigure(0,weight=1)
        sidebar=ttk.Frame(workspace_frame,style='Sidebar.TFrame',padding=(12,20),width=190)
        sidebar.grid(row=0,column=0,sticky='ns')
        sidebar.columnconfigure(0,weight=1)
        self.navigation={}
        self.notebook = ttk.Notebook(workspace_frame,style='Workspace.TNotebook')
        self.notebook.grid(row=0,column=1,sticky="nsew",padx=24,pady=20)
        self.tabs = {}
        for name in ("overview", "connection", "browsers", "diagnostics"):
            tab = ttk.Frame(self.notebook, padding=(0, 4))
            tab.columnconfigure(0, weight=1)
            self.notebook.add(tab, text=self.t(name))
            self.tabs[name] = tab
            navigation=self.button(sidebar,name,lambda key=name:self.select_tab(key),style='Navigation.TButton')
            navigation.grid(sticky='ew',pady=(0,6),ipady=3)
            self.navigation[name]=navigation
        self.notebook.bind('<<NotebookTabChanged>>',self.highlight_navigation)
        overview = self.tabs["overview"]
        self.label(overview, "status", style="Muted.TLabel").grid(sticky="w")
        self.activity = tk.StringVar()
        ttk.Label(overview, textvariable=self.activity, style="Status.TLabel", wraplength=500).grid(sticky="w", pady=(8, 20))
        self.label(overview, "queue", style="Heading.TLabel").grid(sticky="w", pady=(0, 8))
        self.delivery = tk.StringVar()
        ttk.Label(overview, textvariable=self.delivery, wraplength=500).grid(sticky="w")
        self.pause = tk.BooleanVar(value=self.client.state.get("paused", False))
        pause = ttk.Checkbutton(overview, variable=self.pause, command=lambda: self.client.state.set("paused", self.pause.get()))
        self.bind_text(pause, "pause")
        pause.grid(sticky="w", pady=16)
        self.label(overview, "notice", style="Muted.TLabel", wraplength=500).grid(sticky="w")
        self.update_detail = tk.StringVar()
        ttk.Label(overview, textvariable=self.update_detail, style="Muted.TLabel", wraplength=500).grid(sticky="w", pady=(20, 0))
        connection = self.tabs["connection"]
        self.label(connection, "company_code").grid(sticky="w")
        self.code = ttk.Entry(connection)
        self.code.insert(0, company_code)
        if self.install and company_code:
            self.code.state(["readonly"])
        self.code.grid(sticky="ew", pady=(4, 12))
        self.label(connection, "employee_key").grid(sticky="w")
        key_row=ttk.Frame(connection)
        key_row.grid(sticky='ew',pady=(4,12));key_row.columnconfigure(0,weight=1)
        self.key = ttk.Entry(key_row, show="*")
        self.key.grid(row=0,column=0,sticky="ew")
        attach(self.key,lambda:self.language)
        attach(self.code,lambda:self.language)
        self.paste_button=self.button(key_row,'paste',self.paste_key)
        self.paste_button.grid(row=0,column=1,padx=(8,0))
        self.enroll_button = self.button(connection, "connect", self.enroll, style="Primary.TButton")
        self.enroll_button.grid(sticky="w")
        self.enrollment_status = tk.StringVar()
        ttk.Label(connection, textvariable=self.enrollment_status, wraplength=500).grid(sticky="w", pady=16)
        browsers = self.tabs["browsers"]
        self.label(browsers, "browser_status", style="Heading.TLabel").grid(sticky="w", pady=(0, 16))
        self.browser_summary = tk.StringVar()
        ttk.Label(browsers, textvariable=self.browser_summary, wraplength=580, justify='left').grid(sticky='w', pady=(0,12))
        self.browser_rows = ttk.Treeview(browsers, columns=('browser','version','connection'), show='headings', height=4, selectmode='none')
        for column in ('browser','version','connection'):
            self.browser_rows.column(column, width=170, minwidth=100, stretch=True)
        self.browser_rows.grid(sticky='ew', pady=(0,12))
        self.button(browsers, 'check_connection', self.check_browsers).grid(sticky='w', pady=(0,8))
        self.button(browsers, "connect_browsers", self.connect_browsers, style="Primary.TButton").grid(sticky="w")
        self.button(browsers, "extension_folder", lambda: self.open(extension_folder())).grid(sticky="w", pady=12)
        ttk.Separator(browsers).grid(sticky="ew", pady=12)
        self.label(browsers, "browser_help", style="Heading.TLabel").grid(sticky="w", pady=(0, 12))
        self.button(browsers, "guide", self.open_guide).grid(sticky="w")
        diagnostics = self.tabs["diagnostics"]
        self.label(diagnostics, "technical_details", style="Heading.TLabel").grid(sticky="w", pady=(0, 12))
        self.detail = tk.StringVar()
        ttk.Label(diagnostics, textvariable=self.detail, wraplength=650, justify="left").grid(sticky="w")
        self.button(diagnostics, "retry", self.retry_rejected).grid(sticky="w", pady=16)
        footer = ttk.Frame(frame, padding=(24, 12))
        footer.grid(sticky="ew")
        self.button(footer, "guide", self.open_guide).pack(side="left")
        self.version_label = ttk.Label(footer, style="Muted.TLabel")
        self.version_label.pack(side="left", padx=16)
        self.button(footer, "quit", self.close).pack(side="right")
        if not self.client.state.get("identity"):
            self.notebook.select(connection)
        self.worker = Worker(client)
        self.worker.start()
        self.root.protocol("WM_DELETE_WINDOW", self.hide)
        self.root.after(100, self.refresh)
        self.root.update_idletasks()
        self.root.minsize(max(840, self.root.winfo_reqwidth()), max(600, self.root.winfo_reqheight()))

    def select_tab(self, name):
        self.notebook.select(self.tabs[name])
        self.highlight_navigation()

    def highlight_navigation(self, _event=None):
        selected=self.notebook.select()
        for name, button in self.navigation.items():
            button.configure(style='Selected.Navigation.TButton' if str(self.tabs[name])==selected else 'Navigation.TButton')

    def paste_key(self):
        if not paste(self.key):
            self.input_error = 'clipboard_empty'
        else:
            self.input_error = ''
            self.key.focus_set()
        self.render_status()

    def t(self, key, **values):
        return translate(self.language, key, **values)

    def bind_text(self, widget, key):
        widget.configure(text=self.t(key))
        self.translations.append((widget, key))
        return widget

    def label(self, parent, key, **options):
        return self.bind_text(ttk.Label(parent, **options), key)

    def button(self, parent, key, command, **options):
        return self.bind_text(ttk.Button(parent, command=command, **options), key)

    def change_language(self, _event=None):
        self.language = next(key for key, name in LANGUAGES.items() if name == self.language_select.get())
        self.client.state.set("language", self.language)
        for widget, key in self.translations:
            widget.configure(text=self.t(key))
        for key, tab in self.tabs.items():
            self.notebook.tab(tab, text=self.t(key))
        self.render_status()
        if self.tray:
            self.tray.stop()
            self.tray = None
            self.start_tray()

    def open_guide(self):
        try:
            path = (extension_folder() / "setup.html").resolve(strict=True)
            if not webbrowser.open(path.as_uri() + "#lang=" + self.language):
                raise OSError("No browser")
        except (OSError, webbrowser.Error):
            messagebox.showerror("SOFT Tracking", self.t("package_missing"))

    def enroll(self):
        if self.enrollment_thread and self.enrollment_thread.is_alive():
            return
        code, key = self.code.get().strip(), self.key.get().strip()
        import re
        if not re.fullmatch('[a-f0-9]{64}',key):
            self.input_error = 'invalid_employee_key'
            self.render_status()
            return
        self.input_error = ''
        self.key.delete(0, "end")
        self.enroll_button.state(["disabled"])
        def task():
            try:
                self.client.enroll(code, key)
                self.client.state.set("enrollment_message", "connected")
                self.client.state.set("enrollment_error", "")
            except Exception as error:
                self.client.state.set("enrollment_message", "connect_failed")
                self.client.state.set("enrollment_error", str(error) if isinstance(error, ValueError) else "")
        self.enrollment_thread = threading.Thread(target=task, name="Enrollment", daemon=True)
        self.enrollment_thread.start()

    def refresh(self):
        if self.closing:
            return
        if self.install:
            stop = read_json(self.install / "stop-request.json", {})
            if stop.get("token") and stop["token"] == os.environ.get("SOFT_TRACKING_RUN_TOKEN"):
                self.close()
                return
            if (self.install / "show-window.json").exists():
                (self.install / "show-window.json").unlink(missing_ok=True)
                self.root.deiconify()
                self.root.lift()
        self.render_status()
        self.root.after(1000, self.refresh)

    def render_status(self):
        status = self.client.status()
        self.version_label.configure(text=self.t("version") + " " + status["version"])
        identity = status["identity"]
        self.identity.set(identity["company_name"] + " / " + identity["user_name"] if identity else self.t("unregistered"))
        if identity:
            self.enroll_button.state(["disabled"])
            self.code.state(["disabled"])
            self.key.state(["disabled"])
            self.paste_button.state(['disabled'])
        elif not self.enrollment_thread or not self.enrollment_thread.is_alive():
            self.enroll_button.state(["!disabled"])
        queue = status["queue"]
        active = status["policy"].get("tracking") or status["policy"].get("interactions")
        self.activity.set(self.t(status.get('collection_reason', "recording" if active else "paused")))
        self.delivery.set(self.t("pending") + ": " + str(queue["pending"]) + "\n" + self.t("rejected") + ": " + str(queue["rejected"]))
        enrollment = self.client.state.get("enrollment_message", "")
        self.enrollment_status.set(self.t(self.input_error) if self.input_error else (self.t(enrollment) if enrollment in ("connected", "connect_failed") else enrollment))
        error = status["error"]
        if error == "server_unavailable" or error.startswith("Server unavailable or registration rejected"):
            error = self.t("server_unavailable")
        self.detail.set(self.delivery.get() + "\n\n" + error + "\n" + self.client.state.get("enrollment_error", ""))
        rows = connections(self.client)
        for column,key in [('browser','browsers'),('version','version'),('connection','connection')]:
            self.browser_rows.heading(column, text=self.t(key))
        desired = [row['profile'] for row in rows]
        for item in self.browser_rows.get_children():
            if item not in desired:
                self.browser_rows.delete(item)
        for row in rows:
            label = 'browser_storage_error' if row.get('error') else 'browser_connected' if row['connected'] else 'browser_stale'
            values = (row['family'], row['version'], self.t(label))
            if self.browser_rows.exists(row['profile']):
                self.browser_rows.item(row['profile'], values=values)
            else:
                self.browser_rows.insert('', 'end', iid=row['profile'], values=values)
        summary = self.t('browser_storage_error' if any(row['connected'] and row.get('error') for row in rows) else 'browser_connected' if any(row['connected'] for row in rows) else 'browser_waiting')
        self.browser_summary.set(summary + ('\n' + self.t(self.browser_check) if self.browser_check else ''))
        self.detail.set(self.detail.get() + '\n' + summary)
        if self.install:
            update=read_json(self.install / "update-status.json",{})
            if update:
                state = update.get("state", "")
                key = "update_" + {"installed": "active", "rolled_back": "rollback"}.get(state, state)
                from .i18n import TEXT
                label = self.t(key if key in TEXT["en"] else "update_unknown")
                self.update_detail.set(self.t("update") + ": " + label + " " + update.get("version", ""))
                self.detail.set(self.detail.get() + "\n" + self.update_detail.get())

    def check_browsers(self):
        if self.browser_check_thread and self.browser_check_thread.is_alive():
            return
        self.browser_check = 'browser_checking'
        identity = self.client.state.get('identity')
        def check():
            import struct
            from .native_host import ALLOWED_ORIGIN
            try:
                if not self.install or not all(valid for _,valid in registration(self.install, self.client.state.root)):
                    self.browser_check = 'browser_repair'
                    return
                host = self.install / ('SoftTrackingHost.exe' if os.name == 'nt' else 'soft-tracking-host')
                payload = b'{"action":"status"}'
                reply = subprocess.run([str(host),ALLOWED_ORIGIN],input=struct.pack('=I',len(payload))+payload,capture_output=True,timeout=30,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                if reply.returncode or len(reply.stdout)<4:
                    raise ValueError('Native host failed')
                size=struct.unpack('=I',reply.stdout[:4])[0]
                result=json.loads(reply.stdout[4:4+size])
                if not result.get('ok') or result['status'].get('identity')!=identity:
                    raise ValueError('Native registration mismatch')
                self.browser_check = 'browser_host_ready'
            except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
                self.browser_check = 'browser_repair'
        self.browser_check_thread=threading.Thread(target=check,name='BrowserDiagnostics',daemon=True)
        self.browser_check_thread.start()

    def start_tray(self):
        from .ui.tray import TrayController, TrayAction
        if not TrayController.is_supported():
            return False
        from PIL import Image
        asset = Path(__file__).parent / "assets" / "tray_light.png"
        self.tray = TrayController("SOFT Tracking", [
            TrayAction(self.t("open"), lambda: self.root.after(0, self.root.deiconify)),
            TrayAction(self.t("quit"), lambda: self.root.after(0, self.close))
        ], icon_light=Image.open(asset), icon_dark=Image.open(asset))
        if self.tray.start():
            return True
        self.tray=None
        return False

    def retry_rejected(self):
        self.client.outbox.retry_rejected()
        self.client.state.set("retry_generation",int(time.time()*1000))

    def hide(self):
        if self.tray:
            self.root.withdraw()
        else:
            self.close()

    def connect_browsers(self):
        try:
            host = (self.install or Path(sys.executable).parent) / ("SoftTrackingHost.exe" if os.name == "nt" else "soft-tracking-host")
            names = register_host(host, self.client.state.root)
            messagebox.showinfo("SOFT Tracking", self.t("browser_registered", browsers=", ".join(names)))
        except Exception as error:
            messagebox.showerror("SOFT Tracking", str(error))

    def open(self, path):
        try:
            open_path(path)
        except Exception:
            messagebox.showerror("SOFT Tracking", self.t("package_missing"))

    def close(self):
        if self.closing:
            return
        self.closing = True
        self.worker.stopping.set()
        self.root.withdraw()
        if self.tray:
            self.tray.stop()
        self.worker.join(timeout=30)
        if self.enrollment_thread:
            self.enrollment_thread.join(timeout=15)
        self.root.destroy()

    def run(self, start_hidden=False):
        try:
            self.start_tray()
        except Exception:
            self.tray = None
        if start_hidden and self.tray:
            self.root.withdraw()
        self.root.mainloop()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0].startswith("chrome-extension://"):
        from .native_host import main as host_main
        return host_main(argv[0])
    parser = argparse.ArgumentParser(description="SOFT Tracking desktop application (development)")
    parser.add_argument("--company-code", default=company_code_from_filename(sys.executable if getattr(sys, "frozen", False) else sys.argv[0]))
    parser.add_argument("--autostart", action="store_true")
    parser.add_argument("--health-check", action="store_true")
    args = parser.parse_args(argv)
    install = Path(os.environ["SOFT_TRACKING_INSTALL"]) if os.environ.get("SOFT_TRACKING_INSTALL") else None
    if install:
        profile = read_json(install / "enrollment.json", {})
        if args.company_code and profile.get("company_code") and args.company_code != profile["company_code"]:
            raise ValueError("Conflicting company installation codes")
        args.company_code = profile.get("company_code", args.company_code)
    if args.health_check:
        return health_check(install)
    with SingleInstance(workspace() / "agent.lock"):
        client = Client()
        app = Desktop(client, args.company_code)
        if os.environ.get("SOFT_TRACKING_HEALTH"):
            app.root.update_idletasks()
            atomic_json(Path(os.environ["SOFT_TRACKING_HEALTH"]), {"ready": True})
        try:
            app.run(start_hidden=args.autostart)
        finally:
            if not app.worker.is_alive() and (not app.enrollment_thread or not app.enrollment_thread.is_alive()):
                client.close()
    return 0


def health_check(install):
    import struct
    from .native_host import ALLOWED_ORIGIN
    from .core.release_manager import executable_name
    def phase(value):
        if os.environ.get('SOFT_TRACKING_HEALTH'):
            atomic_json(Path(os.environ['SOFT_TRACKING_HEALTH']+'.phase'),{'phase':value})
    phase('tk_start')
    root = tk.Tk()
    root.withdraw()
    root.update_idletasks()
    root.destroy()
    phase('tk_ready')
    if install:
        host = Path(sys.executable).parent / executable_name(native=True)
        payload = b'{"action":"status"}'
        phase('native_start')
        result = subprocess.run([str(host), ALLOWED_ORIGIN], input=struct.pack("=I",len(payload))+payload,
                                capture_output=True, timeout=20,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0)
        if result.returncode != 0 or len(result.stdout)<4:
            return 1
        length = struct.unpack("=I",result.stdout[:4])[0]
        if not json.loads(result.stdout[4:4+length]).get("ok"):
            return 1
    phase('complete')
    if os.environ.get("SOFT_TRACKING_HEALTH"):
        atomic_json(Path(os.environ["SOFT_TRACKING_HEALTH"]), {"ready":True})
    return 0
