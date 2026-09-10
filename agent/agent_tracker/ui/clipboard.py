import sys
import tkinter as tk
from ..i18n import translate


def paste(entry):
    if entry.instate(['disabled']) or entry.instate(['readonly']):
        return False
    try:
        value=entry.clipboard_get().strip()
        if not value or len(value)>4096:
            return False
        if entry.selection_present():
            entry.delete('sel.first','sel.last')
        entry.insert('insert',value)
        return True
    except tk.TclError:
        return False


def attach(entry, language):
    def command(action):
        if action=='paste':
            paste(entry)
        elif action=='select_all':
            entry.selection_range(0,'end');entry.icursor('end')
        else:
            entry.event_generate('<<'+action.title()+'>>')
        return 'break'
    def keys(event):
        action={86:'paste',65:'select_all',67:'copy',88:'cut'}.get(event.keycode) if sys.platform=='win32' else None
        if action:
            return command(action)
    def menu(event):
        popup=tk.Menu(entry,tearoff=False)
        for action in ('cut','copy','paste','select_all'):
            popup.add_command(label=translate(language(),action),command=lambda value=action:command(value))
        try:
            popup.tk_popup(event.x_root,event.y_root)
        finally:
            popup.grab_release()
    entry.bind('<Control-KeyPress>',keys,add='+')
    entry.bind('<<Paste>>',lambda _event:command('paste'))
    entry.bind('<Button-3>',menu)
    return keys
