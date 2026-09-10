from ...i18n import TEXT

EXTRA = {
    'connect': ['Connect', 'Подключить', 'Připojit', 'Ulash'],
    'company': ['Company', 'Компания', 'Společnost', 'Kompaniya'],
    'employee': ['Employee', 'Сотрудник', 'Zaměstnanec', 'Xodim'],
    'browser': ['Browser', 'Браузер', 'Prohlížeč', 'Brauzer'],
    'settings': ['Settings', 'Настройки', 'Nastavení', 'Sozlamalar'],
    'receiving': ['Data delivery', 'Получение данных', 'Doručení dat', 'Maʼlumotlarni yetkazish'],
    'check_again': ['Check again', 'Проверить снова', 'Ověřit znovu', 'Qayta tekshirish'],
    'open_dashboard': ['Open dashboard', 'Открыть дашборд', 'Otevřít přehled', 'Panelni ochish'],
    'received': ['Session received by server', 'Сессия получена сервером', 'Relace doručena serveru', 'Seans serverga yetkazildi'],
    'waiting_session': ['Waiting for a website session', 'Ожидаем сессию с сайта', 'Čekání na relaci webu', 'Sayt seansi kutilmoqda'],
    'last_session': ['Last confirmed session', 'Последняя подтверждённая сессия', 'Poslední potvrzená relace', 'Oxirgi tasdiqlangan seans'],
    'last_confirmation': ['Server confirmation', 'Подтверждение сервера', 'Potvrzení serveru', 'Server tasdigʻi'],
    'no_confirmation': ['No sessions received yet', 'Сессии ещё не получены', 'Zatím nebyly přijaty žádné relace', 'Seanslar hali olinmagan'],
    'auto_update': ['Automatic updates', 'Автообновление', 'Automatické aktualizace', 'Avtomatik yangilash'],
    'help': ['Help', 'Помощь', 'Nápověda', 'Yordam'],
    'details': ['Connection details', 'Подробнее о соединении', 'Podrobnosti připojení', 'Ulanish tafsilotlari'],
    'company_installer': ['Company installer', 'Компания из установщика', 'Instalátor společnosti', 'Kompaniya oʻrnatuvchisi'],
    'checking': ['Checking...', 'Проверяем...', 'Ověřování...', 'Tekshirilmoqda...'],
    'not_connected': ['Not connected', 'Не подключено', 'Nepřipojeno', 'Ulanmagan'],
    'last_contact': ['Last connection', 'Последнее подключение', 'Poslední spojení', 'Oxirgi ulanish'],
    'await_browser': ['Waiting for extension', 'Ожидаем расширение', 'Čekání na rozšíření', 'Kengaytma kutilmoqda'],
    'company_rules': ['Company settings', 'Настройки компании', 'Nastavení společnosti', 'Kompaniya sozlamalari'],
    'on': ['Enabled', 'Включено', 'Zapnuto', 'Yoqilgan'],
    'off': ['Disabled', 'Выключено', 'Vypnuto', 'Oʻchirilgan'],
    'web_time': ['Website activity', 'Время на сайтах', 'Aktivita na webech', 'Saytlardagi faollik'],
    'clicks': ['Clicks and forms', 'Клики и формы', 'Kliknutí a formuláře', 'Bosishlar va shakllar'],
    'fields': ['Field values', 'Значения полей', 'Hodnoty polí', 'Maydon qiymatlari'],
    'programs': ['Application inventory', 'Список программ', 'Seznam aplikací', 'Dasturlar roʻyxati'],
    'allowed_sites': ['Allowed websites', 'Разрешённые сайты', 'Povolené weby', 'Ruxsat etilgan saytlar'],
    'no_sites': ['No websites enabled', 'Нет разрешённых сайтов', 'Žádné povolené weby', 'Ruxsat etilgan saytlar yoʻq'],
    'saved_local': ['Waiting to send', 'Ожидают отправки', 'Čeká na odeslání', 'Yuborish kutilmoqda'],
    'needs_attention': ['Needs attention', 'Требует внимания', 'Vyžaduje pozornost', 'Eʼtibor talab etiladi'],
    'seconds': ['s', 'с', 's', 's'],
    'check_complete': ['Connection checked', 'Подключение проверено', 'Připojení ověřeno', 'Ulanish tekshirildi'],
    'connecting': ['Connecting...', 'Подключаем...', 'Připojování...', 'Ulanmoqda...'],
    'company_disabled': ['Time tracking is disabled in company settings', 'Учёт времени выключен в настройках компании', 'Sledování času je vypnuté v nastavení společnosti', 'Vaqt hisobi kompaniya sozlamalarida oʻchirilgan'],
    'employee_disabled': ['Time tracking is disabled for this employee', 'Учёт времени выключен для сотрудника', 'Sledování času je pro zaměstnance vypnuté', 'Xodim uchun vaqt hisobi oʻchirilgan'],
}


def messages(language):
    language = language if language in ('en', 'ru', 'cs', 'uz') else 'en'
    index = ('en', 'ru', 'cs', 'uz').index(language)
    return dict(TEXT[language], **{key: values[index] for key, values in EXTRA.items()})
