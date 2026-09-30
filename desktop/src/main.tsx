import {lazy, Suspense, useEffect, useRef, useState, type ReactNode} from 'react';
import {createRoot} from 'react-dom/client';
import {Button, Chip, Input, Label, ListBox, ProgressBar, ScrollShadow, Select, Spinner, TextField, Tooltip} from '@heroui/react';
import {ArrowLeft, ArrowRight, Check, CheckCheck, CircleHelp, Clock3, Download, ExternalLink, FolderOpen, Globe2, Link, Minus, Monitor, Play, RefreshCw, Settings2, ShieldCheck, X, AlertCircle, Building2, UserRound, UserRoundPen, Square, ChevronRight} from 'lucide-react';
import brand from '../../extension/icons/icon128.png';
import {invoke, isPreview, nativeFrame, type View} from './bridge';
import {languages, text, type Language, type Message} from './locale';
import {connectionState, viewIsBusy} from './connection-state';
import './styles.css';
const Guide = lazy(() => import('./Guide').then(module => ({default: module.Guide})));

function App() {
  const [view, setView] = useState<View | null>(null), [failed, setFailed] = useState(false);
  const [page, setPage] = useState('connection'), [key, setKey] = useState(''), [code, setCode] = useState('');
  const [localBusy, setLocalBusy] = useState(false), [localError, setLocalError] = useState(false);
  const [switching, setSwitching] = useState(false), [confirmStop, setConfirmStop] = useState(false);
  const started = useRef(false), autoInstalled = useRef(false), mounted = useRef(true);
  const language = view?.language || 'en', t = (id: Message) => text(language, id);
  useEffect(() => {
    mounted.current = true;
    let timer: ReturnType<typeof setTimeout>;
    const refresh = async () => {
      try {
        let state = await invoke('status');
        if (!mounted.current) return;
        if (!started.current) {
          await invoke('ready');
          started.current = true;
          // ready may start legacy detection and may return only an acknowledgement.
          state = await invoke('status');
        }
        if (!mounted.current) return;
        if (!autoInstalled.current && state.mode === 'installer' && state.phase === 'waiting' && !viewIsBusy(state) && !state.error && !state.errorCode && !state.errorText && /^(?:[a-f0-9]{32}|[a-f0-9]{64})$/.test(state.code)) {
          autoInstalled.current = true;
          state = await invoke('install', {code: state.code});
        }
        if (mounted.current) {setView(state); setFailed(false);}
      } catch {if (mounted.current) setFailed(true);}
      if (mounted.current) timer = setTimeout(refresh, 1500);
    };
    void refresh();
    return () => {mounted.current = false; clearTimeout(timer);};
  }, []);
  useEffect(() => {
    const media = matchMedia('(prefers-color-scheme: dark)');
    const update = () => {
      const dark = media.matches;
      document.documentElement.classList.toggle('dark', dark);
      document.documentElement.dataset.theme = dark ? 'dark' : 'light';
      document.documentElement.style.colorScheme = dark ? 'dark' : 'light';
    };
    update(); media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);
  useEffect(() => {document.documentElement.lang = language;}, [language]);
  useEffect(() => {if (view?.message === 'employee_changed') {setSwitching(false); setKey('');}}, [view?.message]);
  async function act(action: string, input = {}) {
    setLocalBusy(true); setLocalError(false);
    try {const state = await invoke(action, input); setView(state); setFailed(false); if (action === 'enroll') setKey('');}
    catch {setLocalError(true);}
    finally {setLocalBusy(false);}
  }
  const busy = localBusy || Boolean(view && viewIsBusy(view));
  const date = (timestamp?: number) => timestamp ? new Date(timestamp * 1000).toLocaleString(language, {day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit'}) : '';
  const iconButton = (label: string, icon: ReactNode, fn: () => void, pressed?: boolean, disabled = false) => <Tooltip delay={300} isDisabled={disabled}><Tooltip.Trigger role="presentation" tabIndex={-1}><Button isIconOnly size="sm" variant={pressed ? 'secondary' : 'ghost'} aria-label={label} aria-pressed={pressed} isDisabled={disabled} onPress={fn}>{icon}</Button></Tooltip.Trigger><Tooltip.Content>{label}</Tooltip.Content></Tooltip>;
  const languageControl = <Select aria-label={t('language')} value={language} onChange={value => void act('preferences', {language: value as Language})} className="language-select">
    <Select.Trigger><Globe2 size={15} aria-hidden="true"/><Select.Value/><Select.Indicator/></Select.Trigger>
    <Select.Popover><ListBox>{Object.entries(languages).map(([id, name]) => <ListBox.Item key={id} id={id} textValue={name}><Label>{name}</Label><ListBox.ItemIndicator/></ListBox.Item>)}</ListBox></Select.Popover>
  </Select>;
  const actionErrors: Record<string, Message> = {setup_migration_failed: 'setupMigrationFailed', employee_switch_not_ready: 'switchBlocked', employee_switch_pending_activity: 'switchPending', stop_pending_activity: 'stopPending', browser_not_found: 'browserMissing', browser_open_failed: 'browserMissing'};
  const errorCopy = failed || localError ? t('genericError') : view?.errorCode ? `${t('supportCode')}: ${view.errorCode}` : view?.error && actionErrors[view.error] ? t(actionErrors[view.error]) : view?.errorText || (view?.error ? t('genericError') : '');
  const error = errorCopy && <div className="notice error" role="alert"><AlertCircle size={18}/><span>{errorCopy}</span></div>;
  const titlebar = !nativeFrame && <header className="titlebar"><span><img src={brand} alt=""/>SOFT Tracking</span><div>{isPreview && <small>{t('preview')}</small>}{iconButton(t('minimize'), <Minus size={16}/>, () => void window.tracking?.window('minimize'))}{iconButton(t('close'), <X size={16}/>, () => void window.tracking?.window('close'))}</div></header>;

  if (!view) return <div className="app-shell">{titlebar}<main className="loading" role="status">{failed ? <AlertCircle className="danger" size={28}/> : <Spinner/>}<p>{failed ? t('genericError') : t('loading')}</p></main></div>;
  const setupProgress: Message = view.phase === 'detecting' ? 'setupDetecting' : view.phase === 'migrating' ? 'setupMigrating' : 'installing';
  const setupComplete = view.phase === 'complete' && !viewIsBusy(view) && !errorCopy;
  if (view.mode === 'installer') return <div className="app-shell installer-shell">{titlebar}
    {page === 'help' ? <div className="installer-guide"><header className="guide-header"><Button isIconOnly variant="ghost" aria-label={t('back')} onPress={() => setPage('connection')}><ArrowLeft size={19}/></Button><h1>{t('help')}</h1></header><Suspense fallback={<Spinner/>}><Guide language={language} busy={busy} onAction={act} extensionAvailable={view.phase === 'complete'}/></Suspense></div> : <main className="installer-main">
      <img src={brand} className="installer-brand" alt="SOFT Tracking"/>
      <h1>{t(viewIsBusy(view) ? setupProgress : errorCopy || view.phase === 'failed' ? 'installFailed' : setupComplete ? 'installationComplete' : 'installTitle')}</h1>
      <p className="muted">{t('installSubtitle')}</p>
      <div className="install-status">
        {viewIsBusy(view) ? <><ProgressBar aria-label={t(setupProgress)} isIndeterminate><ProgressBar.Track><ProgressBar.Fill/></ProgressBar.Track></ProgressBar><div className="install-progress-label" role="status"><Spinner size="sm"/><span>{t(setupProgress)}</span></div></> : setupComplete ? <div className="installed-symbol"><CheckCheck size={32}/></div> : null}
        {error}
        {!view.code && !viewIsBusy(view) && view.phase !== 'complete' && <><TextField value={code} onChange={setCode} className="code-field"><Label>{t('employeeKey')}</Label><Input autoComplete="off" spellCheck={false} aria-describedby="install-key-help"/></TextField><p id="install-key-help" className="muted">{t('oneKeyHelp')}</p>{code.trim() && !/^(?:[a-f0-9]{32}|[a-f0-9]{64})$/.test(code.trim()) && <p role="alert">{t('invalidInstallKey')}</p>}</>}
        {!viewIsBusy(view) && (view.phase === 'complete' ? <Button isDisabled={busy} onPress={() => void act('launch')} className="wide-button">{t('openApp')}<ArrowRight size={18}/></Button> : <Button isDisabled={busy || !/^(?:[a-f0-9]{32}|[a-f0-9]{64})$/.test(view.code || code.trim())} onPress={() => void act('install', {code: view.code || code.trim()})} className="wide-button"><Download size={18}/>{t('install')}</Button>)}
      </div>
      <div className="install-includes"><span><Check size={16}/>{t('files')}</span><span><Check size={16}/>{t('extension')}</span></div>
      <p className="install-safety"><ShieldCheck size={16}/>{t('keepData')}</p>
    </main>}
    <footer className="installer-footer">{languageControl}{iconButton(t('help'), <CircleHelp size={18}/>, () => setPage('help'))}</footer>
  </div>;

  const receipt = view.receipt || {}, browsers = view.browsers || [];
  const connectedBrowsers = browsers.filter(row => row.connected === true && !row.error);
  const status = connectionState(view, Boolean(busy), failed || localError);
  const hasReceipt = status.receipt === 'recent' || status.receipt === 'stale';
  return <div className="app-shell">{titlebar}<div className="workspace">
    <aside className="sidebar"><div className="brand"><img src={brand} alt=""/><strong>SOFT<br/>Tracking</strong></div>
      <nav>{([['connection', Link], ['browsers', Globe2], ['settings', Settings2]] as const).map(([id, Icon]) => <Button key={id} variant="ghost" aria-label={t(id)} aria-current={page === id ? 'page' : undefined} onPress={() => setPage(id)}><Icon size={19}/><span>{t(id)}</span></Button>)}</nav>
      <Button className="help-button" variant="ghost" aria-label={t('help')} aria-current={page === 'help' ? 'page' : undefined} onPress={() => setPage('help')}><CircleHelp size={18}/><span>{t('help')}</span></Button>
    </aside>
    <div className="content-column"><header className="page-header"><h1>{t(page as Message)}</h1><div className="header-controls">{languageControl}</div></header>
    <ScrollShadow className="content-scroll" hideScrollBar={false}>
      <main className={'page-content' + (page === 'connection' && view.enrolled ? ' connection-page' : '')}>{(page !== 'connection' || !view.enrolled) && error}
      {(page !== 'connection' || !view.enrolled) && view.message === 'legacy_cleanup_warning' && <div className="notice" role="status"><AlertCircle size={18}/><span>{t('legacy_cleanup_warning')}</span></div>}
      {page === 'connection' && (!view.enrolled ? <div className="activation"><div className="activation-icon"><Link size={26}/></div><h2>{t('enterCode')}</h2><p className="muted">{t('activationInfo')}</p>
        {view.company && <div className="company-line"><Building2 size={18}/>{view.company}</div>}
        <form onSubmit={event => {event.preventDefault(); void act('enroll', {code: view.code || code, key: key.trim()});}}>
          <TextField value={key} onChange={setKey} isRequired><Label>{t('employeeKey')}</Label><Input autoFocus autoComplete="off" spellCheck={false} className="key-input"/></TextField>
          <Button type="submit" isDisabled={busy || !/^[a-f0-9]{64}$/.test(key.trim())}>{busy ? <Spinner size="sm"/> : <ArrowRight size={18}/>} {t('activate')}</Button>
        </form><div className="privacy-notice"><ShieldCheck size={20}/><p>{t('scopeNotice')}</p></div>
      </div> : <>
        <section className={'connection-status state-' + status.tone} aria-labelledby="connection-status-title">
          <div className="status-icon" aria-hidden="true">{status.tone === 'success' ? <CheckCheck size={27}/> : status.tone === 'accent' ? <RefreshCw size={27} className="spin"/> : <AlertCircle size={27}/>}</div>
          <div className="status-copy" role="status" aria-live="polite" aria-atomic="true" aria-busy={status.tone === 'accent'}><Chip color={status.tone} variant="soft" size="sm"><Chip.Label>{t('connectionState')}</Chip.Label></Chip><h2 id="connection-status-title">{t(status.title)}</h2>
            <p>{status.detail === 'genericError' && errorCopy ? errorCopy : t(status.detail)}</p>
          </div>
          <div className={'status-action' + (status.action === 'check' ? ' status-refresh' : '')}>{status.action === 'check'
            ? iconButton(t('refreshStatus'), <RefreshCw size={18}/>, () => void act('check'), undefined, Boolean(busy))
            : <Button variant="secondary" isDisabled={busy} onPress={() => status.action === 'browsers' ? setPage('browsers') : void act(status.action)}>{status.action === 'browsers' ? <Globe2 size={17}/> : status.action === 'resume' ? <Play size={17}/> : <RefreshCw size={17}/>} {t(status.action)}</Button>}</div>
        </section>
        <div className="identity-rows">{[
          {id: 'company', icon: Building2, name: t('company'), value: view.company},
          {id: 'employee', icon: UserRound, name: t('employee'), value: view.employee},
          {id: 'browser', icon: Globe2, name: t('browser'), value: connectedBrowsers.length ? [...new Set(connectedBrowsers.map(row => row.family))].join(', ') : t('waiting')},
        ].map(row => <div className="identity-row" key={row.id}><row.icon size={19} aria-hidden="true"/><span>{row.name}</span><strong>{row.value || '-'}</strong>{row.id === 'employee' && iconButton(t('switchEmployee'), <UserRoundPen size={16}/>, () => {setSwitching(!switching); setKey('');}, switching, Boolean(busy))}</div>)}</div>
        {switching && <form className="employee-switch" onSubmit={event => {event.preventDefault(); void act('switch-employee', {key: key.trim()});}}><h3>{t('switchEmployee')}</h3><p className="muted">{t('switchNotice')}</p><TextField value={key} onChange={setKey} isRequired><Label>{t('employeeKey')}</Label><Input autoFocus autoComplete="off" spellCheck={false}/></TextField><div className="actions"><Button type="submit" isDisabled={busy || !/^[a-f0-9]{64}$/.test(key.trim())}><UserRoundPen size={16}/>{t('switchEmployee')}</Button><Button variant="ghost" isDisabled={busy} onPress={() => {setSwitching(false); setKey('');}}>{t('cancel')}</Button></div></form>}
        <section className="receipt-section" data-receipt={status.receipt}><h3>{t('confirmed')}</h3>{hasReceipt ? <><div className="receipt-title"><Globe2 size={18}/><strong>{receipt.hostname}</strong><span>{Math.round(receipt.end_timestamp! - receipt.timestamp!)} {t('seconds')}</span></div><p className="muted time-range"><time>{date(receipt.timestamp)}</time><ArrowRight size={13}/><time>{date(receipt.end_timestamp)}</time></p><div className="delivery-line"><ShieldCheck size={16}/><span>{t('delivered')}</span><time>{date(receipt.confirmed_at)}</time></div></> : <p className="receipt-empty">{t('noConfirmedSession')}</p>}</section>
        <div className="queue-row"><span><Clock3 size={17}/>{t('pending')}<strong>{view.pending}</strong></span><span><AlertCircle size={17}/>{t('rejected')}<strong>{view.rejected}</strong></span></div>
        <div className="actions connection-actions"><Button variant="secondary" isDisabled={!(view.domains || []).some(domain => /\.(kommo\.com|amocrm\.ru)$/.test(domain))} onPress={() => void act('open', {target: 'dashboard'})}>{t('dashboard')}<ExternalLink size={16}/></Button></div>
      </>)}
      {page === 'browsers' && <><p className="muted section-intro">{t('browserNotice')}</p><div className="browser-list">{browsers.length ? browsers.map((row, index) => <div key={index} className="browser-row"><Globe2 size={25}/><div><strong>{row.family}</strong><p className="muted">{row.version} · {t('lastContact')}: {date(row.last_seen)}</p></div><span className={row.connected && !row.error ? 'success' : 'warning-text'}>{t(row.connected && !row.error ? 'connected' : 'waiting')}</span>{iconButton(t('browserPage'), <ExternalLink size={17}/>, () => void act('browser-page', {browser: row.family}))}</div>) : <div className="empty-state">{t('noBrowsers')}</div>}</div><div className="actions"><Button onPress={() => void act('repair')} isDisabled={busy}><RefreshCw size={17}/>{t('repair')}</Button><Button variant="secondary" onPress={() => void act('open', {target: 'extension'})}><FolderOpen size={17}/>{t('extensionFolder')}</Button><Button variant="ghost" onPress={() => setPage('help')}>{t('help')}<ChevronRight size={16}/></Button></div></>}
      {page === 'help' && <Suspense fallback={<Spinner/>}><Guide language={language} busy={busy} onAction={act}/></Suspense>}
      {page === 'settings' && <section className="settings-section"><div className="setting-row"><span>{t('autostart')}</span><span className={view.admin?.autostart?.registered ? 'success' : 'warning-text'}>{t(view.admin?.autostart?.registered ? 'on' : 'off')}</span></div><div className="agent-control"><Button variant="secondary" isDisabled={busy} onPress={() => setConfirmStop(true)}><ShieldCheck size={17}/>{t('stopAgent')}</Button><p className="muted">{t('stopNotice')}</p></div>{confirmStop && <div className="actions stop-confirmation"><Button isDisabled={busy} onPress={() => {setConfirmStop(false); void act('stop-agent');}}><Square size={16}/>{t('stopAgent')}</Button><Button variant="ghost" onPress={() => setConfirmStop(false)}>{t('cancel')}</Button></div>}{view.message.startsWith('stop_') && <p role="status" className="notice warning">{t('stopCancelled')}</p>}{view.admin && !view.admin.force_kill_protected && <p className="protection-note muted">{t('perUserProtection')}</p>}</section>}
      {page === 'settings' && <><section className="settings-section"><h3>{t('scope')}</h3><p className="muted section-intro">{t('scopeNotice')}</p>{([['webTime', 'tracking'], ['clicks', 'interactions'], ['fields', 'field_values'], ['inventory', 'app_inventory']] as [Message, string][]).map(([label, flag]) => <div key={flag} className="setting-row"><span>{t(label)}</span><span className={view.policy?.[flag] ? 'success' : 'muted'}>{t(view.policy?.[flag] ? 'on' : 'off')}</span></div>)}</section><section className="settings-section"><h3>{t('domains')}</h3>{view.domains?.length ? <ul className="scope-list">{view.domains.map(domain => <li key={domain}><Globe2 size={15}/>{domain}</li>)}</ul> : <p className="muted">{t('empty')}</p>}<h3>{t('programs')}</h3>{view.programs?.length ? <ul className="scope-list">{view.programs.map(program => <li key={program}><Monitor size={15}/>{program}</li>)}</ul> : <p className="muted">{t('empty')}</p>}</section><Button variant="secondary" onPress={() => void act('retry')} isDisabled={busy}><RefreshCw size={17}/>{t('retry')}</Button></>}
      </main>
    </ScrollShadow><footer className="status-footer"><span><RefreshCw size={14}/>{t('update')}<span className="dot-separator">·</span>{t(({active: 'installed', installed: 'installed', downloading: 'downloading', error: 'updateError', rolled_back: 'rollback', registration: 'waiting'} as Record<string, Message>)[view.update || ''] || 'checking')}</span><span>{view.version}</span></footer></div>
  </div></div>;
}
createRoot(document.getElementById('root')!).render(<App/>);
