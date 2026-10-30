"""Repackage the pinned ARM pilot as a single Apple Installer document."""
import argparse
import hashlib
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import urllib.request
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SOURCE_TAG = 'macos-arm-pilot-3.5.2-rc.3-41093512076-1'
SOURCE_SHA256 = '3473783093b043936066d9f2b318c54e96727309e41e846fd1d979a773ffbaaa'
IDENTIFIER = 'com.soft.tracking.setup.arm-pilot'
PACKAGE_NAME = 'SOFT-Tracking-Setup-ARM.pkg'


def run(*args):
    subprocess.run([str(arg) for arg in args], check=True, timeout=180)


def distribution():
    document = ET.Element('installer-gui-script', {'minSpecVersion': '2'})
    ET.SubElement(document, 'title').text = 'SOFT Tracking Setup'
    ET.SubElement(document, 'welcome', {'file': 'welcome.html'})
    ET.SubElement(document, 'conclusion', {'file': 'conclusion.html'})
    ET.SubElement(document, 'options', {'customize': 'never', 'require-scripts': 'true',
                                      'rootVolumeOnly': 'true', 'hostArchitectures': 'arm64'})
    ET.SubElement(document, 'domains', {'enable_localSystem': 'true',
                                      'enable_currentUserHome': 'false', 'enable_anywhere': 'false'})
    ET.SubElement(ET.SubElement(document, 'allowed-os-versions'), 'os-version', {'min': '13.0'})
    ET.SubElement(ET.SubElement(document, 'choices-outline'), 'line', {'choice': 'setup'})
    choice = ET.SubElement(document, 'choice', {'id': 'setup', 'visible': 'false'})
    ET.SubElement(choice, 'pkg-ref', {'id': IDENTIFIER})
    reference = ET.SubElement(document, 'pkg-ref', {'id': IDENTIFIER, 'version': '3.5.2', 'auth': 'Root'})
    reference.text = 'component.pkg'
    return ET.tostring(document, encoding='utf-8', xml_declaration=True)


def instructions(resources):
    texts = {
        'en': (
            'This package prepares SOFT Tracking Setup for the currently logged-in user. '
            'Only install it if it came from SOFT and you trust it. It is not notarized by Apple. '
            'It verifies the bundled application and permits its private working copy to run. '
            'It does not disable macOS protection or install Python. Administrator authorization is required.',
            'Preparation is complete. In the SOFT Tracking window, enter your employee key and click Install. '
            'Wait for completion, then open SOFT Tracking. The agent is NOT installed merely by opening '
            'the key-entry window. Closing that window before Install cancels agent installation.'),
        'cs': (
            'Tento balíček připraví instalátor SOFT Tracking pro aktuálně přihlášeného uživatele. '
            'Instalujte jej pouze tehdy, pokud pochází od SOFT a důvěřujete mu. Balíček není notarizován společností Apple. '
            'Ověří aplikaci a povolí spuštění její soukromé pracovní kopie. Nevypíná ochranu macOS '
            'a neinstaluje Python. Je nutné oprávnění správce.',
            'Příprava byla dokončena. V okně SOFT Tracking zadejte klíč zaměstnance a klikněte na Instalovat. '
            'Počkejte na dokončení a otevřete SOFT Tracking. Samotné otevření okna pro zadání klíče agenta '
            'NEINSTALUJE. Zavřením tohoto okna před instalací instalaci agenta zrušíte.'),
        'ru': (
            'Этот пакет подготовит установщик SOFT Tracking для пользователя, который сейчас работает за Mac. '
            'Продолжайте только если получили пакет от SOFT и доверяете ему. Пакет не заверен Apple. '
            'Он проверит приложение и разрешит запуск его отдельной рабочей копии. Защита macOS '
            'останется включённой; устанавливать Python не требуется. Потребуется пароль администратора.',
            'Подготовка завершена. В открывшемся окне SOFT Tracking введите ключ сотрудника и нажмите «Установить». '
            'Дождитесь завершения и откройте SOFT Tracking. Само открытие окна с ключом ещё НЕ устанавливает '
            'агент. Если закрыть его до нажатия «Установить», установка агента будет отменена.'),
    }
    for language, paragraphs in texts.items():
        folder = resources / (language + '.lproj')
        folder.mkdir(parents=True)
        for name, paragraph in zip(('welcome.html', 'conclusion.html'), paragraphs):
            page = ET.Element('html')
            head = ET.SubElement(page, 'head')
            ET.SubElement(head, 'meta', {'charset': 'utf-8'})
            body = ET.SubElement(page, 'body', {'style': 'font-family:Helvetica;font-size:14px;padding:16px'})
            ET.SubElement(body, 'h1', {'style': 'font-size:24px'}).text = 'SOFT Tracking'
            ET.SubElement(body, 'p').text = paragraph
            (folder / name).write_bytes(ET.tostring(page, encoding='utf-8', method='html'))


def build(output, source_image=None):
    from release import require_github_runner
    require_github_runner()
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise RuntimeError('Native Apple Silicon runner required')
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='soft-pkg-build-') as temporary:
        work = Path(temporary)
        image = work / 'source.dmg'
        if source_image is not None:
            source_image = Path(source_image)
            if not source_image.is_file() or source_image.is_symlink():
                raise ValueError('Expected a locally built disk image')
            shutil.copyfile(source_image, image)
        else:
            url = 'https://github.com/SalesOfTech/tracking-client/releases/download/' + SOURCE_TAG + '/SOFT-Tracking-Setup.dmg'
            digest = hashlib.sha256()
            total = 0
            with urllib.request.urlopen(url, timeout=60) as response, image.open('xb') as stream:
                for block in iter(lambda: response.read(1024 * 1024), b''):
                    total += len(block)
                    if total > 512 * 1024 * 1024:
                        raise ValueError('Oversized source image')
                    digest.update(block)
                    stream.write(block)
            if digest.hexdigest() != SOURCE_SHA256:
                raise ValueError('Pinned source image digest mismatch')
        mount = work / 'mount'
        mount.mkdir()
        run('hdiutil', 'attach', '-readonly', '-nobrowse', '-mountpoint', mount, image)
        try:
            app = mount / 'SOFT-Tracking-Setup.app'
            run('codesign', '--verify', '--deep', '--strict', app)
            scripts = work / 'scripts'
            shutil.copytree(ROOT / 'tools/macos_pkg_scripts', scripts)
            for script in scripts.iterdir():
                if not script.is_file() or script.is_symlink():
                    raise ValueError('Unexpected package script')
                script.chmod(0o755)
            run('ditto', app, scripts / app.name)
            component = work / 'component.pkg'
            run('pkgbuild', '--nopayload', '--scripts', scripts, '--identifier', IDENTIFIER,
                '--version', '3.5.2', component)
            spec = work / 'distribution.xml'
            spec.write_bytes(distribution())
            resources = work / 'resources'
            instructions(resources)
            package = output / PACKAGE_NAME
            run('productbuild', '--distribution', spec, '--resources', resources,
                '--package-path', work, package)
            checksum = hashlib.sha256(package.read_bytes()).hexdigest()
            package.with_suffix('.pkg.sha256').write_text(checksum + '  ' + package.name + '\n', encoding='ascii')
        finally:
            run('hdiutil', 'detach', mount)
    print('Built single unsigned ARM PKG. Apple trust approval may still be required. No release/feed changed.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--image', type=Path, help='Use the verified DMG built in this same ARM workflow')
    args = parser.parse_args()
    build(args.output.resolve(), args.image)
