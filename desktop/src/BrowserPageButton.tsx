import {useEffect, useRef, useState} from 'react';
import {Button, Spinner, Tooltip} from '@heroui/react';
import {Check, Copy} from 'lucide-react';
import {browserPage} from './bridge';
import {text, type Language} from './locale';

export function BrowserPageButton({family, language, onAction, compact = false}: {
  family: string;
  language: Language;
  compact?: boolean;
  onAction: (action: string, input?: Record<string, unknown>) => Promise<void>;
}) {
  const [pending, setPending] = useState(false), [feedback, setFeedback] = useState<'copied' | 'failed' | null>(null);
  const inFlight = useRef(false);
  const address = browserPage(family), t = (key: 'copyAddress' | 'addressCopied' | 'copyFailed') => text(language, key);
  useEffect(() => {
    if (feedback !== 'copied') return;
    const timer = setTimeout(() => setFeedback(null), 3000);
    return () => clearTimeout(timer);
  }, [feedback]);
  async function copy() {
    if (!address || inFlight.current) return;
    inFlight.current = true; setPending(true); setFeedback(null);
    try {await onAction('copy-browser-page', {browser: family}); setFeedback('copied');}
    catch {setFeedback('failed');}
    finally {inFlight.current = false; setPending(false);}
  }
  return <div className={'browser-copy' + (compact ? ' browser-copy-compact' : '')}>
    <Tooltip delay={300}><Tooltip.Trigger role="presentation" tabIndex={-1}>
      <Button isIconOnly={compact} size={compact ? 'sm' : 'md'} variant={compact ? 'ghost' : 'secondary'} isDisabled={!address || pending} isPending={pending} aria-label={`${t('copyAddress')}: ${family}`} onPress={() => void copy()}>
        {pending ? <Spinner size="sm"/> : feedback === 'copied' ? <Check size={16}/> : <Copy size={16}/>}
        {!compact && <span>{family}<small>{address}</small></span>}
      </Button>
    </Tooltip.Trigger><Tooltip.Content>{t('copyAddress')}</Tooltip.Content></Tooltip>
    <span className={'copy-feedback' + (feedback === 'failed' ? ' danger' : ' success')} role="status" aria-live="polite" aria-atomic="true">{feedback ? t(feedback === 'copied' ? 'addressCopied' : 'copyFailed') : ''}</span>
  </div>;
}
