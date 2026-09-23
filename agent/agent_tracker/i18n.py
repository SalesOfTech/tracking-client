"""Offline language selection using OS locale, never network geolocation."""
from __future__ import annotations

import locale
import os
import re
import sys

LANGUAGES = {"ru": "Русский", "en": "English", "cs": "Čeština", "uz": "O'zbekcha"}
RUSSIAN_REGIONS = frozenset({"RU", "BY", "KZ", "KG", "TJ", "AM", "AZ", "MD", "TM", "UZ"})


def select_language(locales=(), region="", override=""):
    if override in LANGUAGES:
        return override
    tags = [re.split(r"[.@]", str(tag))[0].replace("_", "-") for tag in locales if tag]
    primary = tags[0].split("-")[0].lower() if tags else ""
    if primary in ("ru", "cs", "uz"):
        return primary
    country = region.upper()
    if not country and tags:
        country = next((part.upper() for part in tags[0].split("-")[1:]
                        if len(part) == 2 and part.isalpha()), "")
    if country:
        return "ru" if country in RUSSIAN_REGIONS else "en"
    return "ru" if primary in ("ru", "be", "kk", "ky", "tg", "hy", "az", "tk") else "en"


def system_locale():
    try:
        if os.name == "nt":
            import ctypes
            name = ctypes.create_unicode_buffer(85)
            country = ctypes.create_unicode_buffer(16)
            kernel = ctypes.windll.kernel32
            kernel.GetUserDefaultLocaleName(name, len(name))
            kernel.GetGeoInfoW(kernel.GetUserGeoID(16), 4, country, len(country), 0)
            tags = [name.value]
            try:
                count, length = ctypes.c_ulong(), ctypes.c_ulong()
                if kernel.GetUserPreferredUILanguages(8, ctypes.byref(count), None, ctypes.byref(length)) and 0 < length.value <= 16384:
                    buffer = ctypes.create_unicode_buffer(length.value)
                    if kernel.GetUserPreferredUILanguages(8, ctypes.byref(count), buffer, ctypes.byref(length)):
                        tags = [tag for tag in buffer[:length.value].split("\0") if tag] or tags
            except AttributeError:
                pass
            return tags, country.value
        if sys.platform == "darwin":
            from Foundation import NSLocale
            return list(NSLocale.preferredLanguages()), str(NSLocale.currentLocale().countryCode() or "")
    except (ImportError, AttributeError, OSError, ValueError):
        pass
    # LC_ALL overrides LC_MESSAGES and LANG; a regional locale is preferable to IP inference.
    tag = os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES") or os.environ.get("LANG")
    if not tag:
        try:
            tag = locale.getlocale()[0]
        except (ValueError, OSError):
            tag = ""
    return [tag] if tag else [], ""


def detect_language(override=""):
    if override in LANGUAGES:
        return override
    tags, region = system_locale()
    return select_language(tags, region, override)


def client_language(saved="", enrollment=None):
    enrollment = enrollment or {}
    # Older installers saved automatic defaults as overrides. Only explicit choices should stick.
    override = saved if saved in LANGUAGES else enrollment.get("language", "") if enrollment.get("language_source") == "manual" else ""
    return detect_language(override)


TEXT = {
    "en": {
        "uninstall_title": "Uninstall SOFT Tracking",
        "uninstall_flush_warning": "Before removal we will try to send queued activity for up to 5 seconds. Offline, failed or remaining unsent data will be lost. Continue?",
        "uninstall_confirm": "Uninstall SOFT Tracking for this Windows account?\n\nThis stops tracking and deletes local settings and unsent activity. Server history and other Windows accounts are not removed. Browser extensions must be removed separately.",
        "uninstall_failed": "Uninstall could not be started. Close SOFT Tracking and retry. No administrator access is required.",
        "uninstall_incomplete": "SOFT Tracking removal could not finish. Some local files or registrations may remain. Close the app and retry from Installed Apps.",
        "overview": "Overview", "connection": "Connection", "browsers": "Browsers", "diagnostics": "Diagnostics",
        "language": "Language", "company_code": "Company installation code", "employee_key": "Employee key",
        "connect": "Connect this PC", "unregistered": "This PC is not registered", "connected": "Connected",
        "recording": "Recording enabled", "paused": "Collection paused / disabled", "pending": "Pending database confirmation",
        "rejected": "Needs attention", "status": "Collection status", "queue": "Delivery", "version": "Version",
        "pause": "Pause collection on this PC", "retry": "Retry unconfirmed events", "quit": "Quit", "open": "Open",
        "connect_browsers": "Connect browsers", "extension_folder": "Extension folder", "guide": "Installation guide",
        "notice": "Work activity collection is controlled by your company's settings.",
        "browser_status": "Browser connection", "browser_help": "Installation and permissions",
        "browser_registered": "Desktop connection registered: {browsers}. Open the installation guide to load the extension once.",
        "package_missing": "The extension package is missing. Use the complete company installer.",
        "server_unavailable": "Server unavailable or registration rejected. Unconfirmed events remain on this PC.",
        "connect_failed": "Could not connect. Check your company code, employee key and server availability.",
        "technical_details": "Technical details", "update": "Update", "setup_title": "SOFT Tracking Setup",
        "setup_subtitle": "Agent and browser extension", "install": "Install", "installing": "Installing...",
        "setup_note": "Keep the original installer filename. It identifies your company.",
        "setup_failed": "Installation could not be completed. Local activity has not been deleted.",
        "setup_code_required": "Use the company installer downloaded from the Tracking widget.",
        "setup_company_conflict": "This PC is connected to another company. Use that company's installer. Local data has been preserved.",
        "setup_company_unavailable": "Could not verify the installer's company. Check your connection and use a fresh download from the widget. Local data has been preserved.",
        "setup_existing_damaged": "The existing installation needs repair. Contact your administrator; do not delete the local history folder.",
        "setup_complete": "Installed. Opening SOFT Tracking...", "launch_failed": "Installed, but could not open the application. Open SOFT Tracking from your applications.",
        "update_checking": "Checking", "update_current": "Up to date", "update_downloading": "Downloading",
        "update_staged": "Ready to install", "update_active": "Installed", "update_rollback": "Previous version restored",
        "update_error": "Update needs attention", "update_idle": "Waiting", "update_unknown": "Update status available in technical details",
    },
    "ru": {
        "uninstall_title": "Удаление SOFT Tracking",
        "uninstall_flush_warning": "Перед удалением будет выполнена попытка отправить накопленную активность с ожиданием до 5 секунд. Без сети, при ошибке или нехватке времени неотправленные данные будут потеряны. Продолжить?",
        "uninstall_confirm": "Удалить SOFT Tracking для этой учётной записи Windows?\n\nОтслеживание будет остановлено, локальные настройки и неотправленная активность будут удалены. История на сервере и другие учётные записи Windows не затрагиваются. Расширения браузера нужно удалить отдельно.",
        "uninstall_failed": "Не удалось начать удаление. Закройте SOFT Tracking и повторите попытку. Права администратора не требуются.",
        "uninstall_incomplete": "Удаление SOFT Tracking не завершено. Некоторые локальные файлы или записи регистрации могли остаться. Закройте приложение и повторите удаление через раздел установленных приложений.",
        "overview": "Обзор", "connection": "Подключение", "browsers": "Браузеры", "diagnostics": "Диагностика",
        "language": "Язык", "company_code": "Код установки компании", "employee_key": "Ключ сотрудника",
        "connect": "Подключить этот ПК", "unregistered": "Этот ПК не подключён", "connected": "Подключено",
        "recording": "Сбор данных включён", "paused": "Сбор приостановлен или отключён", "pending": "Ожидают подтверждения сервера",
        "rejected": "Требуют внимания", "status": "Состояние сбора", "queue": "Отправка данных", "version": "Версия",
        "pause": "Приостановить сбор на этом ПК", "retry": "Повторить отправку событий", "quit": "Выйти", "open": "Открыть",
        "connect_browsers": "Подключить браузеры", "extension_folder": "Папка расширения", "guide": "Инструкция по установке",
        "notice": "Сбор рабочей активности регулируется настройками вашей компании.",
        "browser_status": "Подключение браузеров", "browser_help": "Установка и разрешения",
        "browser_registered": "Подключение настроено: {browsers}. Откройте инструкцию, чтобы один раз установить расширение.",
        "package_missing": "Пакет расширения не найден. Используйте полный установщик вашей компании.",
        "server_unavailable": "Сервер недоступен или регистрация отклонена. Неподтверждённые события остаются на этом ПК.",
        "connect_failed": "Не удалось подключиться. Проверьте код компании, ключ сотрудника и доступность сервера.",
        "technical_details": "Технические сведения", "update": "Обновление", "setup_title": "Установка SOFT Tracking",
        "setup_subtitle": "Агент и расширение браузера", "install": "Установить", "installing": "Установка...",
        "setup_note": "Не меняйте имя установщика: по нему определяется ваша компания.",
        "setup_failed": "Не удалось завершить установку. Локальная история не удалена.",
        "setup_code_required": "Используйте установщик компании, скачанный из виджета Tracking.",
        "setup_company_conflict": "Этот ПК подключён к другой компании. Используйте её установщик. Локальные данные сохранены.",
        "setup_company_unavailable": "Не удалось проверить компанию установщика. Проверьте подключение и скачайте установщик заново из виджета. Локальные данные сохранены.",
        "setup_existing_damaged": "Текущая установка требует восстановления. Обратитесь к администратору; не удаляйте папку с локальной историей.",
        "setup_complete": "Установлено. Открываем SOFT Tracking...", "launch_failed": "Установлено, но приложение не открылось. Запустите SOFT Tracking из списка приложений.",
        "update_checking": "Проверка", "update_current": "Установлена актуальная версия", "update_downloading": "Загрузка",
        "update_staged": "Готово к установке", "update_active": "Установлено", "update_rollback": "Возвращена предыдущая версия",
        "update_error": "Обновление требует внимания", "update_idle": "Ожидание", "update_unknown": "Состояние обновления доступно в технических сведениях",
    },
    "cs": {
        "uninstall_title": "Odinstalovat SOFT Tracking",
        "uninstall_flush_warning": "Před odstraněním se pokusíme odeslat čekající aktivitu s čekáním nejvýše 5 sekund. Bez připojení, při chybě nebo po vypršení času budou zbývající neodeslaná data ztracena. Pokračovat?",
        "uninstall_confirm": "Odinstalovat SOFT Tracking pro tento účet Windows?\n\nSledování se zastaví a místní nastavení i neodeslaná aktivita budou odstraněny. Historie na serveru a ostatní účty Windows zůstanou beze změny. Rozšíření prohlížeče je nutné odstranit samostatně.",
        "uninstall_failed": "Odinstalaci nelze spustit. Zavřete SOFT Tracking a zkuste to znovu. Oprávnění správce nejsou potřeba.",
        "uninstall_incomplete": "Odinstalace SOFT Tracking nebyla dokončena. Některé místní soubory nebo registrační záznamy mohou zůstat. Zavřete aplikaci a zkuste odinstalaci znovu v seznamu nainstalovaných aplikací.",
        "overview": "Přehled", "connection": "Připojení", "browsers": "Prohlížeče", "diagnostics": "Diagnostika",
        "language": "Jazyk", "company_code": "Instalační kód společnosti", "employee_key": "Klíč zaměstnance",
        "connect": "Připojit tento počítač", "unregistered": "Tento počítač není připojen", "connected": "Připojeno",
        "recording": "Sběr dat je zapnutý", "paused": "Sběr dat je pozastavený nebo vypnutý", "pending": "Čeká na potvrzení serveru",
        "rejected": "Vyžaduje pozornost", "status": "Stav sběru dat", "queue": "Odesílání dat", "version": "Verze",
        "pause": "Pozastavit sběr na tomto počítači", "retry": "Znovu odeslat nepotvrzené události", "quit": "Ukončit", "open": "Otevřít",
        "connect_browsers": "Připojit prohlížeče", "extension_folder": "Složka rozšíření", "guide": "Návod k instalaci",
        "notice": "Sběr pracovní aktivity se řídí nastavením vaší společnosti.",
        "browser_status": "Připojení prohlížečů", "browser_help": "Instalace a oprávnění",
        "browser_registered": "Připojení nastaveno: {browsers}. Otevřete návod a jednorázově nainstalujte rozšíření.",
        "package_missing": "Balíček rozšíření nebyl nalezen. Použijte úplný instalátor vaší společnosti.",
        "server_unavailable": "Server není dostupný nebo byla registrace odmítnuta. Nepotvrzené události zůstávají na tomto počítači.",
        "connect_failed": "Připojení se nezdařilo. Zkontrolujte kód společnosti, klíč zaměstnance a dostupnost serveru.",
        "technical_details": "Technické údaje", "update": "Aktualizace", "setup_title": "Instalace SOFT Tracking",
        "setup_subtitle": "Agent a rozšíření prohlížeče", "install": "Nainstalovat", "installing": "Instalace...",
        "setup_note": "Neměňte název instalátoru. Určuje vaši společnost.",
        "setup_failed": "Instalaci se nepodařilo dokončit. Místní historie nebyla odstraněna.",
        "setup_code_required": "Použijte instalátor společnosti stažený z widgetu Tracking.",
        "setup_company_conflict": "Tento počítač je připojen k jiné společnosti. Použijte její instalátor. Místní data byla zachována.",
        "setup_company_unavailable": "Společnost instalátoru se nepodařilo ověřit. Zkontrolujte připojení a stáhněte nový instalátor z widgetu. Místní data byla zachována.",
        "setup_existing_damaged": "Stávající instalace vyžaduje opravu. Kontaktujte správce; nemažte složku s místní historií.",
        "setup_complete": "Nainstalováno. Otevírá se SOFT Tracking...", "launch_failed": "Nainstalováno, ale aplikaci nelze otevřít. Spusťte SOFT Tracking ze seznamu aplikací.",
        "update_checking": "Kontrola", "update_current": "Aktuální verze", "update_downloading": "Stahování",
        "update_staged": "Připraveno k instalaci", "update_active": "Nainstalováno", "update_rollback": "Obnovena předchozí verze",
        "update_error": "Aktualizace vyžaduje pozornost", "update_idle": "Čekání", "update_unknown": "Stav aktualizace je v technických údajích",
    },
    "uz": {
        "uninstall_title": "SOFT Tracking dasturini o'chirish",
        "uninstall_flush_warning": "O'chirishdan oldin navbatdagi faollikni yuborishga urinib, ko'pi bilan 5 soniya kutamiz. Internet bo'lmasa, xato yuz bersa yoki vaqt tugasa, yuborilmagan ma'lumotlar yo'qoladi. Davom etilsinmi?",
        "uninstall_confirm": "SOFT Tracking ushbu Windows hisobi uchun o'chirilsinmi?\n\nKuzatuv to'xtatiladi, mahalliy sozlamalar va yuborilmagan faollik o'chiriladi. Serverdagi tarix va boshqa Windows hisoblari o'zgarmaydi. Brauzer kengaytmalarini alohida o'chirish kerak.",
        "uninstall_failed": "O'chirishni boshlab bo'lmadi. SOFT Tracking dasturini yoping va qayta urinib ko'ring. Administrator huquqlari talab qilinmaydi.",
        "uninstall_incomplete": "SOFT Tracking dasturini o'chirish yakunlanmadi. Ba'zi mahalliy fayllar yoki ro'yxat yozuvlari qolgan bo'lishi mumkin. Dasturni yoping va o'rnatilgan ilovalar ro'yxatidan qayta urinib ko'ring.",
        "overview": "Umumiy", "connection": "Ulanish", "browsers": "Brauzerlar", "diagnostics": "Diagnostika",
        "language": "Til", "company_code": "Kompaniya o'rnatish kodi", "employee_key": "Xodim kaliti",
        "connect": "Bu kompyuterni ulash", "unregistered": "Bu kompyuter ulanmagan", "connected": "Ulandi",
        "recording": "Ma'lumot yig'ish yoqilgan", "paused": "Ma'lumot yig'ish to'xtatilgan yoki o'chirilgan", "pending": "Server tasdig'i kutilmoqda",
        "rejected": "E'tibor talab qiladi", "status": "Yig'ish holati", "queue": "Ma'lumot yuborish", "version": "Versiya",
        "pause": "Bu kompyuterda yig'ishni to'xtatish", "retry": "Tasdiqlanmagan hodisalarni qayta yuborish", "quit": "Chiqish", "open": "Ochish",
        "connect_browsers": "Brauzerlarni ulash", "extension_folder": "Kengaytma papkasi", "guide": "O'rnatish yo'riqnomasi",
        "notice": "Ish faoliyati ma'lumotlarini yig'ish kompaniyangiz sozlamalari bilan boshqariladi.",
        "browser_status": "Brauzer ulanishi", "browser_help": "O'rnatish va ruxsatlar",
        "browser_registered": "Ulanish sozlandi: {browsers}. Kengaytmani bir marta o'rnatish uchun yo'riqnomani oching.",
        "package_missing": "Kengaytma paketi topilmadi. Kompaniyangizning to'liq o'rnatish dasturidan foydalaning.",
        "server_unavailable": "Server mavjud emas yoki ro'yxatdan o'tish rad etildi. Tasdiqlanmagan hodisalar bu kompyuterda saqlanadi.",
        "connect_failed": "Ulanib bo'lmadi. Kompaniya kodi, xodim kaliti va server mavjudligini tekshiring.",
        "technical_details": "Texnik ma'lumotlar", "update": "Yangilanish", "setup_title": "SOFT Tracking o'rnatish",
        "setup_subtitle": "Agent va brauzer kengaytmasi", "install": "O'rnatish", "installing": "O'rnatilmoqda...",
        "setup_note": "O'rnatish fayli nomini o'zgartirmang. U kompaniyangizni aniqlaydi.",
        "setup_failed": "O'rnatishni yakunlab bo'lmadi. Mahalliy tarix o'chirilmadi.",
        "setup_code_required": "Tracking vidjetidan yuklab olingan kompaniya o'rnatish faylidan foydalaning.",
        "setup_company_conflict": "Bu kompyuter boshqa kompaniyaga ulangan. O'sha kompaniyaning o'rnatish faylidan foydalaning. Mahalliy ma'lumotlar saqlandi.",
        "setup_company_unavailable": "O'rnatish faylining kompaniyasini tekshirib bo'lmadi. Ulanishni tekshiring va vidjetdan yangi fayl yuklab oling. Mahalliy ma'lumotlar saqlandi.",
        "setup_existing_damaged": "Joriy o'rnatishni tiklash kerak. Administratorga murojaat qiling; mahalliy tarix papkasini o'chirmang.",
        "setup_complete": "O'rnatildi. SOFT Tracking ochilmoqda...", "launch_failed": "O'rnatildi, lekin dastur ochilmadi. Dasturlar ro'yxatidan SOFT Trackingni ishga tushiring.",
        "update_checking": "Tekshirilmoqda", "update_current": "Eng so'nggi versiya", "update_downloading": "Yuklanmoqda",
        "update_staged": "O'rnatishga tayyor", "update_active": "O'rnatildi", "update_rollback": "Oldingi versiya tiklandi",
        "update_error": "Yangilanish e'tibor talab qiladi", "update_idle": "Kutilmoqda", "update_unknown": "Yangilanish holati texnik ma'lumotlarda",
    },
}


for _language, _values in {
    'en': {'paste': 'Paste', 'copy': 'Copy', 'cut': 'Cut', 'select_all': 'Select all', 'clipboard_empty': 'The clipboard is empty or unavailable.', 'invalid_employee_key': 'Use the 64-character employee key from the Tracking settings. Legacy extension keys are not supported.', 'setup_close_required': 'Close SOFT Tracking and try the installation again. Local data has been preserved.', 'setup_upgrade_failed': 'The update could not start. The previous version was restored; local data has been preserved.', 'setup_wrong_target': 'Download an installer for the same operating system and architecture as the installed application.', 'update_registration': 'Connect this PC to enable automatic updates'},
    'ru': {'paste': 'Вставить', 'copy': 'Копировать', 'cut': 'Вырезать', 'select_all': 'Выделить всё', 'clipboard_empty': 'Буфер обмена пуст или недоступен.', 'invalid_employee_key': 'Используйте ключ сотрудника из 64 символов из настроек Tracking. Ключи старого расширения не подходят.', 'setup_close_required': 'Закройте SOFT Tracking и повторите установку. Локальные данные сохранены.', 'setup_upgrade_failed': 'Обновление не запустилось. Восстановлена предыдущая версия; локальные данные сохранены.', 'setup_wrong_target': 'Скачайте установщик для той же системы и архитектуры, что и установленное приложение.', 'update_registration': 'Подключите этот ПК для автоматического обновления'},
    'cs': {'paste': 'Vložit', 'copy': 'Kopírovat', 'cut': 'Vyjmout', 'select_all': 'Vybrat vše', 'clipboard_empty': 'Schránka je prázdná nebo nedostupná.', 'invalid_employee_key': 'Použijte 64znakový klíč zaměstnance z nastavení Tracking. Klíče starého rozšíření nejsou podporovány.', 'setup_close_required': 'Zavřete SOFT Tracking a opakujte instalaci. Místní data byla zachována.', 'setup_upgrade_failed': 'Aktualizace se nespustila. Předchozí verze byla obnovena; místní data byla zachována.', 'setup_wrong_target': 'Stáhněte instalátor pro stejný systém a architekturu jako nainstalovaná aplikace.', 'update_registration': 'Připojte tento počítač pro automatické aktualizace'},
    'uz': {'paste': 'Joylashtirish', 'copy': 'Nusxalash', 'cut': 'Kesish', 'select_all': 'Barchasini tanlash', 'clipboard_empty': "Almashish buferi bo'sh yoki mavjud emas.", 'invalid_employee_key': "Tracking sozlamalaridagi 64 belgili xodim kalitidan foydalaning. Eski kengaytma kalitlari mos kelmaydi.", 'setup_close_required': "SOFT Trackingni yoping va o'rnatishni takrorlang. Mahalliy ma'lumotlar saqlandi.", 'setup_upgrade_failed': "Yangilanish ishga tushmadi. Oldingi versiya tiklandi; mahalliy ma'lumotlar saqlandi.", 'setup_wrong_target': "O'rnatilgan dastur bilan bir xil tizim va arxitektura uchun faylni yuklab oling.", 'update_registration': "Avtomatik yangilanishlar uchun bu kompyuterni ulang"},
}.items():
    TEXT[_language].update(_values)


for _key, _values in {
    'legacy_migration_unrecognized': ['Legacy startup is not recognized. Ask your administrator to check it.', 'Не распознан автозапуск старого агента. Нужна проверка администратора.', 'Automatické spuštění starého agenta nebylo rozpoznáno. Požádejte správce o kontrolu.', 'Eski agent avtoishga tushishi aniqlanmadi. Administrator tekshirsin.'],
    'legacy_migration_denied': ['Could not replace the old agent for this Windows account. Check permissions and retry.', 'Не удалось заменить старый агент этой учётной записи Windows. Проверьте права и повторите.', 'Starého agenta tohoto účtu Windows nelze nahradit. Zkontrolujte oprávnění a opakujte akci.', 'Ushbu Windows hisobi eski agentini almashtirib bo‘lmadi. Ruxsatlarni tekshiring va qayta urining.'],
    'legacy_migration_changed': ['The old installation changed during replacement. Ask your administrator to check it before retrying.', 'Старая установка изменилась во время замены. Перед повтором нужна проверка администратора.', 'Stará instalace se během nahrazování změnila. Před opakováním požádejte správce o kontrolu.', 'Almashtirish paytida eski o‘rnatish o‘zgardi. Qayta urinishdan oldin administrator tekshirsin.'],
    'check_connection': ['Check connection', 'Проверить связь', 'Ověřit připojení', 'Ulanishni tekshirish'],
    'inventory_only': ['Only application inventory is enabled', 'Включён только сбор списка программ', 'Zapnutý je pouze seznam aplikací', 'Faqat dasturlar ro‘yxati yig‘iladi'],
    'browser_storage_error': ['Extension storage error', 'Ошибка хранилища расширения', 'Chyba úložiště rozšíření', 'Kengaytma xotirasi xatosi'],
    'browser_connected': ['Extension connected', 'Расширение на связи', 'Rozšíření připojeno', 'Kengaytma ulangan'],
    'browser_stale': ['No recent connection', 'Давно не выходило на связь', 'Bez nedávného spojení', 'Yaqinda ulanmagan'],
    'browser_waiting': ['No extension has connected recently. Open your work browser and allow up to one minute.', 'Расширение пока не выходило на связь. Откройте рабочий браузер и подождите до минуты.', 'Rozšíření se zatím nepřipojilo. Otevřete pracovní prohlížeč a počkejte až minutu.', 'Kengaytma hali ulanmagan. Ish brauzerini ochib, bir daqiqagacha kuting.'],
    'browser_checking': ['Checking local connection...', 'Проверяем локальное подключение...', 'Ověřování místního připojení...', 'Mahalliy ulanish tekshirilmoqda...'],
    'browser_host_ready': ['Local connector works. The table confirms actual extension connections.', 'Локальный модуль связи работает. В таблице показаны реальные подключения расширений.', 'Místní konektor funguje. Tabulka potvrzuje skutečná připojení rozšíření.', 'Mahalliy ulanish moduli ishlaydi. Jadval haqiqiy kengaytma ulanishlarini ko‘rsatadi.'],
    'browser_repair': ['Local connector needs repair. Click Connect browsers, then restart your browser.', 'Нужно восстановить локальное подключение. Нажмите «Подключить браузеры», затем перезапустите браузер.', 'Místní konektor vyžaduje opravu. Klikněte na Připojit prohlížeče a restartujte prohlížeč.', 'Mahalliy ulanishni tiklash kerak. Brauzerlarni ulash tugmasini bosib, brauzerni qayta oching.'],
    'paused_local': ['Paused on this PC', 'Сбор приостановлен на этом ПК', 'Pozastaveno na tomto počítači', 'Bu kompyuterda to‘xtatilgan'],
    'policy_expired': ['Waiting for current company settings', 'Ожидаем актуальные настройки компании', 'Čekání na aktuální nastavení společnosti', 'Kompaniyaning yangi sozlamalari kutilmoqda'],
    'disabled_policy': ['Collection disabled in company or employee settings', 'Сбор выключен в настройках компании или сотрудника', 'Sběr je vypnutý v nastavení společnosti nebo zaměstnance', 'Yig‘ish kompaniya yoki xodim sozlamalarida o‘chirilgan'],
    'collection_error': ['Collection needs attention. Open Diagnostics.', 'Сбор требует внимания. Откройте «Диагностика».', 'Sběr vyžaduje pozornost. Otevřete Diagnostiku.', 'Yig‘ish e’tibor talab qiladi. Diagnostikani oching.'],
}.items():
    for _language, _value in zip(('en', 'ru', 'cs', 'uz'), _values):
        TEXT[_language][_key] = _value


def translate(language, key, **values):
    return TEXT.get(language, TEXT["en"])[key].format(**values)
