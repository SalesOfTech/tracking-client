SOFT Tracking - macOS Apple Silicon - installation pilot

ENGLISH
Requires macOS 13 or later and an Apple Silicon Mac (M1/M2/M3/M4 or later).
This test package is not notarized by Apple. It does not change the stable update feed.

1. Open SOFT-Tracking-Setup and follow the installer.
2. If macOS blocks launch, an administrator can open Terminal, type /bin/bash
   followed by a space, drag Install.command from this disk image into Terminal,
   then press Return. Read the trust prompt. Enter y only if this package came
   from SOFT and you trust it. Enter n to cancel without changing trust settings.
3. Run under the employee's logged-in account, without sudo. Administrator status
   is fine; a root shell is not. The app belongs to the current user.
4. Enter the employee key supplied by your administrator, finish installation,
   and follow the browser extension instructions inside the application.

The fallback verifies code-signature integrity, then removes quarantine only
from its private Setup copy and the verified SOFT engine cache. This is explicit
local trust, not Apple certification. It does not disable Gatekeeper and does not
install Python, Homebrew or Xcode. Do not use this procedure for unknown downloads.

If installation fails, send the displayed error code, macOS version and chip model.
Do not send employee keys. Physical M1 installation and a subsequent live update
must be confirmed before promoting this pilot as a stable release.

ČESKY
Vyžaduje macOS 13 nebo novější a Mac s Apple Silicon (M1/M2/M3/M4 nebo novější).
Tento testovací balíček není notarizován společností Apple. Nemění stabilní kanál aktualizací.

1. Otevřete SOFT-Tracking-Setup a postupujte podle instalačního programu.
2. Pokud macOS spuštění zablokuje, správce může otevřít Terminál, napsat /bin/bash
   a mezeru, přetáhnout soubor Install.command z tohoto obrazu disku do Terminálu
   a stisknout Return. Přečtěte si upozornění. Zadejte y pouze tehdy, pokud balíček
   pochází od SOFT a důvěřujete mu. Volba n zruší instalaci bez změny důvěry.
3. Instalaci spusťte pod přihlášeným účtem zaměstnance, bez sudo. Účet může mít
   oprávnění správce, ale nepoužívejte shell uživatele root. Aplikace patří aktuálnímu uživateli.
4. Zadejte klíč zaměstnance od správce, dokončete instalaci a postupujte podle
   pokynů pro rozšíření prohlížeče uvnitř aplikace.

Pomocný skript ověří integritu podpisu a odstraní karanténu pouze ze své soukromé
kopie instalátoru a ověřené mezipaměti modulu SOFT. Jde o výslovné místní povolení,
nikoli o certifikaci Apple. Gatekeeper se nevypíná. Python, Homebrew ani Xcode
se neinstalují. Tento postup nepoužívejte pro neznámé stažené soubory.

Při chybě nám pošlete zobrazený kód, verzi macOS a typ procesoru. Klíče zaměstnanců
neposílejte. Před stabilním vydáním je třeba potvrdit instalaci na skutečném M1
a následně otestovat aktualizaci na další verzi.
