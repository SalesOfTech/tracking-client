"""Minimal console guidance for the personalized migration tool."""

MESSAGES = {
    'en': {
        'intro': 'SOFT Tracking migration. Run in the employee Windows session, without administrator elevation. No code entry is required.',
        'failed': 'Migration did not complete. Keep both installations. Retry this personalized download after a network failure; otherwise contact your CRM administrator with the standalone-migration.json status. An expired download must be issued again.',
        'complete': 'Migration completed. Legacy rollback files were retained. Check that SOFT Tracking opens correctly.',
        'close': 'Press Enter to close.',
    },
    'ru': {
        'intro': 'Миграция SOFT Tracking. Запускайте в Windows-сеансе сотрудника, без прав администратора. Вводить коды не требуется.',
        'failed': 'Миграция не завершена. Сохраните обе установки. После сетевого сбоя повторите запуск этого персонального файла; в остальных случаях передайте администратору CRM статус из standalone-migration.json. Просроченный файл нужно скачать заново.',
        'complete': 'Миграция завершена. Файлы Legacy для восстановления сохранены. Проверьте, что SOFT Tracking открывается корректно.',
        'close': 'Нажмите Enter, чтобы закрыть окно.',
    },
    'cs': {
        'intro': 'Migrace SOFT Tracking. Spusťte v relaci Windows daného zaměstnance, bez oprávnění správce. Není třeba zadávat kódy.',
        'failed': 'Migrace nebyla dokončena. Ponechte obě instalace. Po výpadku sítě spusťte tento osobní soubor znovu; jinak předejte správci CRM stav ze standalone-migration.json. Po vypršení platnosti stáhněte nový soubor.',
        'complete': 'Migrace dokončena. Soubory Legacy pro obnovení byly zachovány. Ověřte, že se SOFT Tracking správně otevře.',
        'close': 'Stisknutím Enter zavřete okno.',
    },
    'uz': {
        'intro': 'SOFT Tracking migratsiyasi. Xodimning Windows seansida, administrator huquqisiz ishga tushiring. Kod kiritish shart emas.',
        'failed': 'Migratsiya tugamadi. Ikkala dasturni ham saqlang. Tarmoq xatosidan keyin shu shaxsiy faylni qayta ishga tushiring; boshqa holatda CRM administratoriga standalone-migration.json holatini yuboring. Muddati tugagan faylni qayta yuklab oling.',
        'complete': 'Migratsiya tugadi. Tiklash uchun Legacy fayllari saqlandi. SOFT Tracking ochilishini tekshiring.',
        'close': 'Yopish uchun Enter tugmasini bosing.',
    },
}
