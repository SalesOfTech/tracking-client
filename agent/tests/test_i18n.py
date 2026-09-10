import json
import os
import string
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_tracker.i18n import LANGUAGES, TEXT, select_language, translate, system_locale, client_language, detect_language
from agent_tracker.core.client import Client
from agent_tracker.desktop import Desktop
from agent_tracker.installer import InstallerWindow

ROOT = Path(__file__).resolve().parents[2]


class LanguageTests(unittest.TestCase):
    def test_windows_display_language_is_not_the_regional_format(self):
        import ctypes
        from types import SimpleNamespace
        def locale_name(buffer, _length):
            buffer.value = 'en-US'
            return 6
        def country(_geo, _kind, buffer, _length, _language):
            buffer.value = 'RU'
            return 3
        def preferred(_flags, count, buffer, length):
            tags = 'uz-Latn-UZ\0en-US\0\0'
            length._obj.value = len(tags)
            count._obj.value = 2
            if buffer is not None:
                for index, character in enumerate(tags):
                    buffer[index] = character
            return 1
        kernel = SimpleNamespace(GetUserDefaultLocaleName=locale_name, GetUserGeoID=lambda _kind: 203,
                                 GetGeoInfoW=country, GetUserPreferredUILanguages=preferred)
        with patch('agent_tracker.i18n.os.name', 'nt'), patch.object(ctypes, 'windll', SimpleNamespace(kernel32=kernel), create=True):
            tags, region = system_locale()
            self.assertEqual(['uz-Latn-UZ', 'en-US'], tags)
            self.assertEqual('RU', region)
            self.assertEqual('uz', select_language(tags, region))

    def test_shared_language_rules(self):
        cases = json.loads((ROOT / 'extension/tests/locale-cases.json').read_text(encoding='utf-8'))
        for case in cases:
            with self.subTest(case=case):
                self.assertEqual(case['expected'], select_language(case['tags'], override=case.get('override', '')))
        self.assertEqual('ru', select_language(['en-US'], region='KZ'))
        self.assertEqual('ru', select_language(['ru-RU'], region='DE'))
        self.assertEqual('cs', select_language(['cs-CZ'], region='RU'))
        self.assertEqual('uz', select_language(['uz-Latn-UZ'], region='RU'))

    def test_russian_ui_wins_over_region_and_old_automatic_default(self):
        with patch('agent_tracker.i18n.system_locale', return_value=(['ru-RU', 'en-US'], 'NL')):
            self.assertEqual('ru', detect_language())
            self.assertEqual('ru', client_language('', {'language': 'en'}))
            self.assertEqual('en', client_language('en', {'language': 'ru'}))
            self.assertEqual('cs', client_language('', {'language': 'cs', 'language_source': 'manual'}))
            app = InstallerWindow(ROOT / 'extension', 'b' * 32)
            try:
                self.assertEqual('ru', app.language)
                self.assertEqual('', app.language_override)
                app.selector.set(LANGUAGES['cs'])
                app.change_language()
                self.assertEqual('cs', app.language_override)
                app.details_key = 'setup_company_unavailable'
                app.render_language()
                self.assertEqual(translate('cs', app.details_key), app.details.get())
            finally:
                app.root.destroy()

    def test_all_catalog_keys_and_placeholders_match(self):
        formatter = string.Formatter()
        for language in LANGUAGES:
            self.assertEqual(set(TEXT['en']), set(TEXT[language]))
            for key, value in TEXT[language].items():
                self.assertTrue(value.strip(), (language, key))
                fields = lambda text: {item[1] for item in formatter.parse(text) if item[1]}
                self.assertEqual(fields(TEXT['en'][key]), fields(value))

    def screenshot(self, root, name):
        if os.environ.get('TRACKING_UI_SCREENSHOTS') != '1':
            return
        from PIL import ImageGrab
        target = ROOT / 'artifacts/verification'
        target.mkdir(parents=True, exist_ok=True)
        root.update()
        root.lift()
        root.update()
        root.after(250, root.quit)
        root.mainloop()
        x, y = root.winfo_rootx(), root.winfo_rooty()
        ImageGrab.grab(bbox=(x, y, x + root.winfo_width(), y + root.winfo_height())).save(target / (name + '.png'))

    def test_desktop_translates_without_losing_input_or_restarting_worker(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=False):
            client = Client(Path(tmp))
            client.state.set('language', 'en')
            with patch('agent_tracker.desktop.Worker.start') as start, patch('agent_tracker.desktop.Worker.join'):
                app = Desktop(client, 'a' * 32)
                try:
                    app.key.insert(0, 'test-key')
                    for language, label in LANGUAGES.items():
                        app.language_select.set(label)
                        app.change_language()
                        app.root.update_idletasks()
                        self.assertEqual(language, client.state.get('language'))
                        self.assertEqual(translate(language, 'unregistered'), app.identity.get())
                        self.assertEqual('test-key', app.key.get())
                        self.assertEqual('a' * 32, app.code.get())
                        self.assertEqual(translate(language, 'connect'), app.enroll_button.cget('text'))
                        for key, tab in app.tabs.items():
                            self.assertEqual(translate(language, key), app.notebook.tab(tab, 'text'))
                        self.screenshot(app.root, 'desktop-' + language)
                    start.assert_called_once()
                    app.language_select.set(LANGUAGES['cs'])
                    app.change_language()
                    with patch('agent_tracker.desktop.extension_folder', return_value=ROOT / 'extension'), patch('agent_tracker.desktop.webbrowser.open', return_value=True) as open_guide:
                        app.open_guide()
                        self.assertTrue(open_guide.call_args.args[0].endswith('/setup.html#lang=cs'))
                finally:
                    app.root.destroy()
                    client.close()

    def test_installer_has_four_languages_and_preserves_company(self):
        app = InstallerWindow(ROOT / 'extension', 'b' * 32, 'en')
        try:
            for language, label in LANGUAGES.items():
                app.selector.set(label)
                app.change_language()
                app.root.update_idletasks()
                self.assertEqual(translate(language, 'setup_title'), app.root.title())
                self.assertEqual(translate(language, 'install'), app.button.cget('text'))
                self.assertEqual('b' * 32, app.code.get())
                self.screenshot(app.root, 'installer-' + language)
        finally:
            app.root.destroy()

    def test_paste_button_virtual_event_and_russian_keyboard(self):
        import tkinter as tk
        from types import SimpleNamespace
        from agent_tracker.ui.clipboard import attach
        with tempfile.TemporaryDirectory() as tmp, patch('agent_tracker.desktop.Worker.start'):
            client=Client(Path(tmp))
            app=Desktop(client,'a'*32)
            clipboard=patch.object(app.key,'clipboard_get',return_value='  '+'e'*64+'\n')
            clipboard.start()
            try:
                app.root.update()
                app.paste_button.invoke()
                self.assertEqual('e'*64,app.key.get())
                app.key.selection_range(0,'end')
                app.key.event_generate('<<Paste>>')
                self.assertEqual('e'*64,app.key.get())
                keys=attach(app.key,lambda:'ru')
                with patch('agent_tracker.ui.clipboard.sys.platform','win32'):
                    keys(SimpleNamespace(keycode=65,keysym='Cyrillic_ef'))
                    keys(SimpleNamespace(keycode=86,keysym='Cyrillic_em'))
                self.assertEqual('e'*64,app.key.get())
                app.key.delete(0,'end');app.key.insert(0,'old-key')
                app.enroll();app.render_status()
                self.assertEqual(app.t('invalid_employee_key'),app.enrollment_status.get())
                self.assertEqual('old-key',app.key.get())
                app.key.state(['disabled'])
                app.paste_key()
                self.assertEqual('old-key',app.key.get())
            finally:
                clipboard.stop()
                app.root.destroy();client.close()


if __name__ == '__main__':
    unittest.main()
