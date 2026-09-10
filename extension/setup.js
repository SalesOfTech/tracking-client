(function () {
  'use strict';
  const {languages, selectLanguage} = typeof module !== 'undefined' && module.exports ? require('./locale.js') : window.TrackingLanguage;
  const guides = {
    en: {
      language: 'Language', title: 'Installation guide',
      intro: 'Work activity is collected according to your company settings. Its status is visible in the application and extension. This guide works offline.',
      note: 'Use the installer from your company widget. Do not share employee keys or delete local data while events await server confirmation.',
      sections: [
        ['install', 'Install and connect', [
          'In the Tracking widget settings, open Installation. Check the OS and architecture or select another computer. Download the company installer and keep its original filename.',
          'On Windows, open the .exe. On macOS, mount the .dmg and open SOFT Tracking Setup. On Linux, allow the .run file to execute in its file properties. Use a build compatible with your OS.',
          'The current installer is per OS account: install under the employee account, not a separate administrator. This version is not an administrator-protected system service.',
          'Check the company code and select Install. In the application, open Connection, enter the v3 employee key from your administrator and select Connect. Check your company and name. One employee may connect several PCs, only within their company.'
        ]],
        ['browsers', 'Connect the browser', [
          'Open Browsers in the application. Select Connect browsers, then Extension folder. The application and browser must run under the same OS account.',
          'In Chrome, type chrome://extensions; in Edge, edge://extensions. Enable Developer mode. Select Load unpacked and choose the permanent extension folder opened by the application.',
          'Keep Developer mode enabled and use the permanent extension folder for each work browser profile. Open Connection in SOFT Tracking and select Check again. Allow up to one minute for the browser to appear. The last confirmed session shows whether website time has reached the server. The extension toolbar icon opens the application.',
          'Incognito is not tracked. Firefox and Safari are not included; other Chromium browsers require separate compatibility checks. An employee can still disable an unmanaged extension.'
        ]],
        ['permissions', 'OS permissions', [
          'The installer has no OS publisher signature. If Windows or macOS warns you, ask your administrator to verify its source before approving this application. Do not disable system protection globally.',
          'On macOS, check Privacy & Security if Accessibility, Automation or screen information access is requested. Approve only SOFT Tracking and the permissions actually requested. Permissions cannot be granted silently.',
          'Desktop application tracking on Linux requires X11. Wayland is not supported in this release.'
        ]],
        ['updates', 'Updates', [
          'SOFT Tracking checks for updates at startup and while running. Prepared releases install automatically after signature verification, without an extra confirmation dialog. If startup fails, it restores the previous version. Update signatures are separate from OS publisher signing.',
          'The extension package updates together with the agent. Browser activation may wait for local events to synchronize or for permission to reload. Follow browser prompts; changed permissions may require approval.',
          'Old v2 installations are not migrated automatically. Do not install over an existing v3 installation to force an update. Preserve local data until delivery is confirmed.'
        ]],
        ['help', 'Connection or delivery problem', [
          'Check the company, employee name, internet and collection status. The company may have disabled collection, or its permission may have expired while offline.',
          'Open Connection details to see pending events. They stay locally until the server confirms storage. Rejected events need attention. After your administrator resolves the cause, open Settings and select Retry unconfirmed events.',
          'Do not delete application data or the browser profile. Send support the OS, version, error text and pending count, never the employee key. Check free disk space if collection stops.'
        ]]
      ],
      references: 'Platform documentation', chrome: 'Chrome: load an unpacked extension', mac: 'macOS: open an app from an unknown developer'
    },
    ru: {
      language: 'Язык', title: 'Инструкция по установке',
      intro: 'Рабочая активность собирается по настройкам компании. Состояние видно в приложении и расширении. Эта инструкция работает без интернета.',
      note: 'Используйте установщик из виджета вашей компании. Не передавайте ключи сотрудника и не удаляйте локальные данные, пока события ожидают подтверждения сервера.',
      sections: [
        ['install', 'Установка и подключение', [
          'В настройках виджета Tracking откройте «Установка». Проверьте ОС и архитектуру или выберите другой компьютер. Скачайте установщик компании и не меняйте имя файла.',
          'В Windows откройте .exe. В macOS подключите образ .dmg и откройте SOFT Tracking Setup. В Linux разрешите запуск файла .run в его свойствах. Выбирайте сборку, совместимую с вашей ОС.',
          'Текущий установщик работает для одной учётной записи ОС: устанавливайте под сотрудником, а не отдельным администратором. Эта версия пока не является защищённой системной службой.',
          'Проверьте код компании и нажмите «Установить». В приложении откройте «Подключение», введите ключ сотрудника v3 от администратора и нажмите «Подключить». Проверьте компанию и имя. Один сотрудник может подключить несколько ПК, только внутри своей компании.'
        ]],
        ['browsers', 'Подключение браузера', [
          'В приложении откройте «Браузеры», нажмите «Подключить браузеры», затем «Папка расширения». Приложение и браузер должны работать под одной учётной записью ОС.',
          'В Chrome введите chrome://extensions, в Edge: edge://extensions. Включите «Режим разработчика». Нажмите «Загрузить распакованное расширение» и выберите постоянную папку расширения, открытую приложением.',
          'Оставьте режим разработчика включённым и используйте постоянную папку расширения в каждом рабочем профиле. В SOFT Tracking откройте «Подключение» и нажмите «Проверить снова». Браузер должен появиться в течение минуты. Последняя подтверждённая сессия показывает, дошло ли время сайта до сервера. Значок расширения открывает приложение.',
          'Инкогнито не отслеживается. Firefox и Safari пока не входят в выпуск; другие Chromium-браузеры требуют отдельной проверки. Обычный пользователь всё ещё может отключить неуправляемое расширение.'
        ]],
        ['permissions', 'Разрешения ОС', [
          'У установщика нет подписи издателя для ОС. Если Windows или macOS предупреждает об этом, попросите администратора проверить источник перед разрешением запуска. Не отключайте защиту всей системы.',
          'В macOS проверьте «Конфиденциальность и безопасность», если ОС запрашивает универсальный доступ, автоматизацию или сведения об экране. Разрешайте доступ только SOFT Tracking и только по фактическому запросу. Разрешения нельзя выдать незаметно.',
          'Учёт активности приложений в Linux требует X11. Wayland в этом выпуске не поддерживается.'
        ]],
        ['updates', 'Обновления', [
          'SOFT Tracking проверяет обновления при запуске и во время работы. Подготовленные выпуски устанавливаются автоматически после проверки подписи, без дополнительного запроса подтверждения. При ошибке запуска возвращается предыдущая версия. Подпись обновления отличается от подписи издателя для ОС.',
          'Пакет расширения обновляется вместе с агентом. Активация в браузере может ждать синхронизации локальных событий или разрешения перезагрузки. Следуйте запросам браузера: новые разрешения могут требовать подтверждения.',
          'Старые установки v2 не переводятся автоматически. Не устанавливайте поверх существующей v3 ради обновления. Сохраняйте локальные данные до подтверждения доставки.'
        ]],
        ['help', 'Проблемы с подключением или отправкой', [
          'Проверьте компанию, имя сотрудника, интернет и состояние сбора. Компания могла отключить сбор, либо разрешение истекло во время отсутствия связи.',
          'Откройте «Подробнее о соединении»: там показаны ожидающие события. Они остаются локально до подтверждения записи сервером. Отклонённые события требуют проверки. После устранения причины администратором откройте «Настройки» и нажмите «Повторить отправку событий».',
          'Не удаляйте данные приложения или профиль браузера. Передайте поддержке ОС, версию, текст ошибки и число ожидающих событий, но не ключ сотрудника. При остановке сбора проверьте свободное место на диске.'
        ]]
      ],
      references: 'Документация платформ', chrome: 'Chrome: распакованное расширение', mac: 'macOS: приложение неизвестного разработчика'
    },
    cs: {
      language: 'Jazyk', title: 'Návod k instalaci',
      intro: 'Pracovní aktivita se sbírá podle nastavení společnosti. Stav je viditelný v aplikaci i rozšíření. Tento návod funguje bez internetu.',
      note: 'Použijte instalátor z widgetu vaší společnosti. Nesdílejte klíče zaměstnanců a nemažte místní data, dokud události čekají na potvrzení serveru.',
      sections: [
        ['install', 'Instalace a připojení', [
          'V nastavení widgetu Tracking otevřete instalaci. Zkontrolujte OS a architekturu nebo vyberte jiný počítač. Stáhněte firemní instalátor a neměňte název souboru.',
          'Ve Windows otevřete .exe. V macOS připojte obraz .dmg a otevřete SOFT Tracking Setup. V Linuxu povolte spuštění souboru .run v jeho vlastnostech. Použijte sestavení kompatibilní s vaším OS.',
          'Současný instalátor je pro jeden účet OS: instalujte pod zaměstnancem, nikoli samostatným správcem. Tato verze zatím není chráněnou systémovou službou.',
          'Zkontrolujte kód společnosti a zvolte Nainstalovat. V aplikaci otevřete Připojení, zadejte klíč zaměstnance v3 od správce a zvolte Připojit. Zkontrolujte společnost a jméno. Zaměstnanec může připojit více počítačů pouze ve své společnosti.'
        ]],
        ['browsers', 'Připojení prohlížeče', [
          'V aplikaci otevřete Prohlížeče, zvolte Připojit prohlížeče a poté Složka rozšíření. Aplikace a prohlížeč musí běžet pod stejným účtem OS.',
          'V Chromu zadejte chrome://extensions, v Edge edge://extensions. Zapněte Režim pro vývojáře. Zvolte Načíst rozbalené a vyberte trvalou složku rozšíření otevřenou aplikací.',
          'Ponechte režim pro vývojáře zapnutý a používejte stálou složku rozšíření v každém pracovním profilu. V SOFT Tracking otevřete Připojení a zvolte Ověřit znovu. Prohlížeč se má objevit do minuty. Poslední potvrzená relace ukazuje, zda čas webu dorazil na server. Ikona rozšíření otevírá aplikaci.',
          'Anonymní režim se nesleduje. Firefox a Safari nejsou součástí vydání; další prohlížeče Chromium vyžadují samostatné ověření. Běžný uživatel stále může vypnout nespravované rozšíření.'
        ]],
        ['permissions', 'Oprávnění OS', [
          'Instalátor nemá podpis vydavatele pro OS. Pokud Windows nebo macOS zobrazí varování, požádejte správce o ověření zdroje před povolením aplikace. Nevypínejte ochranu celého systému.',
          'V macOS zkontrolujte Soukromí a zabezpečení, pokud OS požaduje Zpřístupnění, Automatizaci nebo údaje o obrazovce. Schvalte jen SOFT Tracking a skutečně vyžadovaná oprávnění. Nelze je udělit skrytě.',
          'Sledování aktivity aplikací v Linuxu vyžaduje X11. Wayland není v tomto vydání podporován.'
        ]],
        ['updates', 'Aktualizace', [
          'SOFT Tracking kontroluje aktualizace při spuštění i během provozu. Připravená vydání se instalují automaticky po ověření podpisu bez dalšího potvrzení. Při chybě spuštění se obnoví předchozí verze. Podpis aktualizace není totéž co podpis vydavatele pro OS.',
          'Balíček rozšíření se aktualizuje s agentem. Aktivace může čekat na synchronizaci místních událostí nebo povolení nového načtení. Řiďte se výzvami prohlížeče; nová oprávnění mohou vyžadovat potvrzení.',
          'Staré instalace v2 se nepřevádějí automaticky. Kvůli aktualizaci neinstalujte přes stávající v3. Uchovejte místní data do potvrzení doručení.'
        ]],
        ['help', 'Problém s připojením nebo odesíláním', [
          'Zkontrolujte společnost, jméno, internet a stav sběru. Společnost mohla sběr vypnout nebo jeho oprávnění mohlo během výpadku vypršet.',
          'V Podrobnostech připojení najdete čekající události. Zůstávají místně do potvrzení uložení serverem. Odmítnuté události vyžadují pozornost. Po vyřešení příčiny správcem otevřete Nastavení a zvolte Znovu odeslat nepotvrzené události.',
          'Nemažte data aplikace ani profil prohlížeče. Podpoře pošlete OS, verzi, chybu a počet čekajících událostí, nikdy klíč zaměstnance. Při zastavení sběru ověřte volné místo na disku.'
        ]]
      ],
      references: 'Dokumentace platforem', chrome: 'Chrome: rozbalené rozšíření', mac: 'macOS: aplikace neznámého vývojáře'
    },
    uz: {
      language: 'Til', title: "O'rnatish yo'riqnomasi",
      intro: "Ish faoliyati kompaniya sozlamalariga muvofiq yig'iladi. Holat dastur va kengaytmada ko'rinadi. Bu yo'riqnoma internetsiz ishlaydi.",
      note: "Kompaniyangiz vidjetidagi o'rnatish dasturidan foydalaning. Xodim kalitini ulashmang. Hodisalar server tasdig'ini kutayotganida mahalliy ma'lumotlarni o'chirmang.",
      sections: [
        ['install', "O'rnatish va ulanish", [
          "Tracking vidjeti sozlamalarida o'rnatishni oching. OT va arxitekturani tekshiring yoki boshqa kompyuterni tanlang. Kompaniya o'rnatish faylini yuklab oling va nomini o'zgartirmang.",
          "Windowsda .exe faylini oching. macOSda .dmg tasvirini ulang va SOFT Tracking Setupni oching. Linuxda .run fayli xususiyatlarida ishga tushirishga ruxsat bering. OTingizga mos versiyadan foydalaning.",
          "Joriy o'rnatish bitta OT hisobi uchun: alohida administrator emas, xodim hisobi ostida o'rnating. Bu versiya hali himoyalangan tizim xizmati emas.",
          "Kompaniya kodini tekshiring va O'rnatishni bosing. Dasturda Ulanishni oching, administratordan olingan v3 xodim kalitini kiriting va Ulashni bosing. Kompaniya va ismingizni tekshiring. Xodim faqat o'z kompaniyasida bir nechta kompyuterni ulashi mumkin."
        ]],
        ['browsers', 'Brauzerni ulash', [
          "Dasturda Brauzerlarni oching, Brauzerlarni ulashni, keyin Kengaytma papkasini bosing. Dastur va brauzer bir xil OT hisobida ishlashi kerak.",
          "Chromeda chrome://extensions, Edgeda edge://extensions kiriting. Developer mode (dasturchi rejimi)ni yoqing. Load unpacked (ochilgan kengaytmani yuklash)ni tanlang va dastur ochgan doimiy kengaytma papkasini ko'rsating.",
          "Dasturchi rejimini yoqilgan qoldiring va har bir ish profilida kengaytmaning doimiy papkasidan foydalaning. SOFT Trackingda Ulanishni ochib, Qayta tekshirishni bosing. Brauzer bir daqiqada ko'rinishi kerak. Oxirgi tasdiqlangan seans sayt vaqti serverga yetganini ko'rsatadi. Kengaytma belgisi dasturni ochadi.",
          "Inkognito kuzatilmaydi. Firefox va Safari relizga kirmaydi; boshqa Chromium brauzerlari alohida tekshiruvni talab qiladi. Oddiy foydalanuvchi boshqarilmaydigan kengaytmani hali ham o'chira oladi."
        ]],
        ['permissions', 'OT ruxsatlari', [
          "O'rnatish faylida OT nashriyotchi imzosi yo'q. Windows yoki macOS ogohlantirsa, dasturga ruxsat berishdan oldin administratordan manbani tekshirishni so'rang. Tizim himoyasini butunlay o'chirmang.",
          "macOS Accessibility, Automation yoki ekran ma'lumotlariga kirishni so'rasa, Privacy & Securityni tekshiring. Faqat SOFT Tracking va haqiqatan so'ralgan ruxsatlarni tasdiqlang. Ularni yashirincha berib bo'lmaydi.",
          "Linuxda dastur faoliyatini kuzatish X11 talab qiladi. Wayland bu relizda qo'llab-quvvatlanmaydi."
        ]],
        ['updates', 'Yangilanishlar', [
          "SOFT Tracking ishga tushganda va ishlash davomida yangilanishlarni tekshiradi. Tayyor relizlar imzo tekshirilgach, qo'shimcha tasdiqsiz avtomatik o'rnatiladi. Ishga tushish xatosida oldingi versiya tiklanadi. Yangilanish imzosi OT nashriyotchi imzosidan farq qiladi.",
          "Kengaytma paketi agent bilan yangilanadi. Faollashish mahalliy hodisalar sinxronlanishini yoki qayta yuklash ruxsatini kutishi mumkin. Brauzer so'rovlariga amal qiling; yangi ruxsatlar tasdiq talab qilishi mumkin.",
          "Eski v2 o'rnatishlari avtomatik ko'chirilmaydi. Yangilash uchun mavjud v3 ustiga o'rnatmang. Yetkazish tasdiqlanmaguncha mahalliy ma'lumotlarni saqlang."
        ]],
        ['help', 'Ulanish yoki yuborish muammosi', [
          "Kompaniya, xodim ismi, internet va yig'ish holatini tekshiring. Kompaniya yig'ishni o'chirgan yoki aloqa yo'qligida ruxsat muddati tugagan bo'lishi mumkin.",
          "Ulanish tafsilotlarida kutilayotgan hodisalarni ko'ring. Ular server saqlashni tasdiqlaguncha mahalliy qoladi. Rad etilgan hodisalar e'tibor talab qiladi. Administrator sababni bartaraf etgach, Sozlamalarda Tasdiqlanmagan hodisalarni qayta yuborishni bosing.",
          "Dastur ma'lumotlari yoki brauzer profilini o'chirmang. Yordam xizmatiga OT, versiya, xato va kutilayotgan hodisalar sonini yuboring, xodim kalitini emas. Yig'ish to'xtasa, diskdagi bo'sh joyni tekshiring."
        ]]
      ],
      references: 'Platforma hujjatlari', chrome: 'Chrome: ochilgan kengaytma', mac: "macOS: noma'lum dasturchi dasturi"
    }
  };
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = {selectLanguage, guides};
    return;
  }
  function element(tag, text) {
    const node = document.createElement(tag);
    node.textContent = text;
    return node;
  }
  const selector = document.getElementById('language');
  const guide = document.getElementById('guide');
  function render(language) {
    const data = guides[language];
    document.documentElement.lang = language;
    document.title = 'SOFT Tracking: ' + data.title;
    document.getElementById('language-label').textContent = data.language;
    selector.value = language;
    guide.replaceChildren(element('h1', data.title), element('p', data.intro));
    const nav = document.createElement('nav');
    nav.setAttribute('aria-label', data.title);
    for (const [id, title] of data.sections) {
      const link = element('a', title);
      link.href = '#lang=' + language + '&section=' + id;
      nav.append(link);
    }
    guide.append(nav, element('aside', data.note));
    for (const [id, title, steps] of data.sections) {
      const section = document.createElement('section');
      section.id = id;
      section.append(element('h2', title));
      const list = document.createElement('ol');
      for (const step of steps) list.append(element('li', step));
      section.append(list);
      guide.append(section);
    }
    const sources = element('p', data.references + ': ');
    sources.className = 'source';
    for (const [label, href] of [[data.chrome, 'https://developer.chrome.com/docs/extensions/get-started/tutorial/hello-world#load-unpacked'], [data.mac, 'https://support.apple.com/102445']]) {
      const link = element('a', label);
      link.href = href;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      sources.append(link, document.createTextNode(' '));
    }
    guide.append(sources);
  }
  function load() {
    const hash = new URLSearchParams(location.hash.slice(1));
    let saved = '';
    try { saved = localStorage.getItem('soft-tracking-guide-language'); } catch (_) { /* Local file storage may be blocked. */ }
    const requested = hash.get('lang') || new URLSearchParams(location.search).get('lang');
    render(selectLanguage(navigator.languages || [navigator.language], languages.includes(requested) ? requested : saved));
    const section = document.getElementById(hash.get('section'));
    if (section && section.tagName === 'SECTION') section.scrollIntoView();
  }
  selector.addEventListener('change', () => {
    try { localStorage.setItem('soft-tracking-guide-language', selector.value); } catch (_) { /* Optional preference only. */ }
    location.hash = 'lang=' + selector.value;
  });
  window.addEventListener('hashchange', load);
  load();
})();
