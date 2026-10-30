SOFT Tracking - macOS Apple Silicon - installation pilot

ENGLISH
Requires macOS 13 or later and an Apple Silicon Mac (M1/M2/M3/M4 or later).
This test package is not notarized by Apple. It does not change the stable update feed.

1. Open the DMG, then double-click SOFT-Tracking-Setup.
2. If macOS blocks launch, open System Settings > Privacy & Security and use
   Open Anyway for SOFT Tracking Setup. Then launch it again if necessary.
   Read the SOFT trust dialog and click Continue only if you trust this download.
   Preparation runs automatically; no Terminal commands are needed in this flow.
3. Run under the employee's logged-in account, without sudo. Administrator status
   is fine; a root shell is not. The app belongs to the current user.
4. Enter the employee key supplied by your administrator and click Install.
   Wait for completion. Opening the key-entry window alone does NOT install the app.
   Closing it before clicking Install cancels installation.
5. After installation, SOFT Tracking is available in your user Applications folder.
   Follow the browser extension instructions inside the application.

The launcher verifies code-signature integrity, then removes quarantine only
from its private Setup copy and the verified SOFT engine cache. This is explicit
local trust, not Apple certification. It does not disable Gatekeeper and does not
install Python, Homebrew or Xcode. Do not use this procedure for unknown downloads.
Install.command is retained only as a support fallback, not the normal installation path.
If Open Anyway does not help, contact support rather than disabling macOS protection.

If installation fails, send the displayed error code, macOS version and chip model.
Do not send employee keys. Physical M1 installation and a subsequent live update
must be confirmed before promoting this pilot as a stable release.

ČESKY
Vyžaduje macOS 13 nebo novější a Mac s Apple Silicon (M1/M2/M3/M4 nebo novější).
Tento testovací balíček není notarizován společností Apple. Nemění stabilní kanál aktualizací.

1. Otevřete DMG a dvakrát klikněte na SOFT-Tracking-Setup.
2. Pokud macOS spuštění zablokuje, otevřete Nastavení systému > Soukromí a zabezpečení
   a zvolte Přesto otevřít pro SOFT Tracking Setup. Případně instalátor spusťte znovu.
   Přečtěte si upozornění SOFT a klikněte na Pokračovat, pouze pokud balíčku důvěřujete.
   Příprava proběhne automaticky; v tomto postupu nejsou potřeba příkazy v Terminálu.
3. Instalaci spusťte pod přihlášeným účtem zaměstnance, bez sudo. Účet může mít
   oprávnění správce, ale nepoužívejte shell uživatele root. Aplikace patří aktuálnímu uživateli.
4. Zadejte klíč zaměstnance od správce a klikněte na Instalovat. Počkejte na dokončení.
   Samotné otevření okna pro zadání klíče aplikaci NEINSTALUJE.
   Zavřením tohoto okna před instalací instalaci zrušíte.
5. Po instalaci najdete SOFT Tracking ve složce Aplikace svého uživatelského účtu.
   Postupujte podle pokynů pro rozšíření prohlížeče uvnitř aplikace.

Spouštěč ověří integritu podpisu a odstraní karanténu pouze ze své soukromé
kopie instalátoru a ověřené mezipaměti modulu SOFT. Jde o výslovné místní povolení,
nikoli o certifikaci Apple. Gatekeeper se nevypíná. Python, Homebrew ani Xcode
se neinstalují. Tento postup nepoužívejte pro neznámé stažené soubory.
Install.command zůstává pouze jako záložní postup pro podporu.
Pokud volba Přesto otevřít nepomůže, kontaktujte podporu; ochranu macOS nevypínejte.

Při chybě nám pošlete zobrazený kód, verzi macOS a typ procesoru. Klíče zaměstnanců
neposílejte. Před stabilním vydáním je třeba potvrdit instalaci na skutečném M1
a následně otestovat aktualizaci na další verzi.
