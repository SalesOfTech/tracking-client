import {Button} from '@heroui/react';
import {ExternalLink, FolderOpen} from 'lucide-react';
import guides from '../../agent/agent_tracker/assets/guide.json';
import {text, type Language} from './locale';

const browserPages = [
  ['Chrome', 'chrome://extensions'], ['Edge', 'edge://extensions'],
  ['Yandex', 'browser://extensions'], ['Opera', 'opera://extensions'],
  ['Brave', 'brave://extensions'], ['Vivaldi', 'vivaldi://extensions'],
  ['Chromium', 'chrome://extensions'], ['Firefox', 'about:addons'],
] as const;

export function Guide({language, busy, onAction, extensionAvailable = true}: {
  language: Language;
  busy?: boolean;
  extensionAvailable?: boolean;
  onAction: (action: string, input?: Record<string, unknown>) => Promise<void>;
}) {
  const guide = guides[language];
  return <article className="guide">
    <p className="muted">{guide.intro}</p>
    <nav className="guide-index" aria-label={text(language, 'help')}>
      {guide.sections.map(section => <a key={section.id} href={'#guide-' + section.id}>{section.title}</a>)}
    </nav>
    {guide.sections.map(section => <section key={section.id} id={'guide-' + section.id}>
      <h2>{section.title}</h2>
      <ol>{section.steps.map((step, index) => <li key={index}>{step}</li>)}</ol>
      {section.id === 'browsers' && <>
        <div className="browser-pages">{browserPages.map(([family, url]) => <Button key={family} variant="secondary" isDisabled={busy} onPress={() => void onAction('browser-page', {browser: family})}><ExternalLink size={15}/><span>{family}<small>{url}</small></span></Button>)}</div>
        <Button variant="ghost" isDisabled={!extensionAvailable} onPress={() => void onAction('open', {target: 'extension'})}><FolderOpen size={16}/>{text(language, 'extensionFolder')}</Button>
      </>}
    </section>)}
    <p className="guide-note muted">{guide.note}</p>
  </article>;
}
