import {Button} from '@heroui/react';
import {FolderOpen} from 'lucide-react';
import guides from '../../agent/agent_tracker/assets/guide.json';
import {text, type Language} from './locale';
import {browserPages} from './bridge';
import {BrowserPageButton} from './BrowserPageButton';

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
        <p className="copy-instructions muted">{text(language, 'copyBrowserInstructions')}</p>
        <div className="browser-pages">{Object.keys(browserPages).map(family => <BrowserPageButton key={family} family={family} language={language} onAction={onAction}/>)}</div>
        <Button variant="ghost" isDisabled={!extensionAvailable} onPress={() => void onAction('open', {target: 'extension'})}><FolderOpen size={16}/>{text(language, 'extensionFolder')}</Button>
      </>}
    </section>)}
    <p className="guide-note muted">{guide.note}</p>
  </article>;
}
