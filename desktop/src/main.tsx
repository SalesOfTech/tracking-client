import {lazy, Suspense, useEffect, useRef, useState, type ReactNode} from 'react';
import {createRoot} from 'react-dom/client';
import {Button, Input, Label, ListBox, ProgressBar, ScrollShadow, Select, Spinner, TextField, Tooltip} from '@heroui/react';
import {Activity, ArrowLeft, ArrowRight, Check, CheckCheck, CheckCircle2, CircleHelp, Clock3, Download, ExternalLink, FolderOpen, Globe2, Link, Minus, Monitor, Play, RefreshCw, Settings2, ShieldCheck, X, AlertCircle, Building2, UserRound, UserRoundPen, Square, ChevronRight} from 'lucide-react';
import brand from '../../extension/icons/icon128.png';
import {invoke, isPreview, nativeFrame, type View} from './bridge';
import {languages, text, type Language, type Message} from './locale';
import './styles.css';
const Guide = lazy(() => import('./Guide').then(module => ({default: module.Guide})));

function App() {
  const [view, setView] = useState<View | null>(null), [failed, setFailed] = useState(false);
  const [page, setPage] = useState('connection'), [key, setKey] = useState(''), [code, setCode] = useState('');
  const [localBusy, setLocalBusy] = useState(false), [localError, setLocalError] = useState(false);
  const [switching, setSwitching] = useState(false), [confirmStop, setConfirmStop] = useState(false);
  const started = useRef(false), mounted = useRef(true);
  const language = view?.language || 'en', t = (id: Message) => text(language, id);
  useEffect(() => {
    mounted.current = true;
    let timer: ReturnType<typeof setTimeout>;
    const refresh = async () => {
      try {
        const state = await invoke('status');
        if (!mounted.current) return;
        setView(state); setFailed(false);
        if (!started.current) {
          started.current = true;
          await invoke('ready');
          if (state.mode === 'installer' && state.code) await invoke('install', {code: state.code});
        }
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
    try {const state = await invoke(action, input); setView(state); if (action === 'enroll') setKey('');}
    catch {setLocalError(true);}
    finally {setLocalBusy(false);}
  }
  const busy = localBusy || view?.busy;
  const date = (timestamp?: number) => timestamp ? new Date(timestamp * 1000).toLocaleString(language, {day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit'}) : '';
  const iconButton = (label: string, icon: ReactNode, fn: () => void, pressed?: boolean) => <Tooltip delay={300}><Button isIconOnly size="sm" variant={pressed ? 'secondary' : 'ghost'} aria-label={label} aria-pressed={pressed} onPress={fn}>{icon}</Button><Tooltip.Content>{label}</Tooltip.Content></Tooltip>;
  const languageControl = <Select aria-label={t('language')} value={language} onChange={value => void act('preferences', {language: value as Language})} className="language-select">
    <Select.Trigger><Globe2 size={15} aria-hidden="true"/><Select.Value/><Select.Indicator/></Select.Trigger>
    <Select.Popover><ListBox>{Object.entries(languages).map(([id, name]) => <ListBox.Item key={id} id={id} textValue={name}><Label>{name}</Label><ListBox.ItemIndicator/></ListBox.Item>)}</ListBox></Select.Popover>
  </Select>;
  const actionErrors: Record<string, Message> = {employee_switch_not_ready: 'switchBlocked', employee_switch_pending_activity: 'switchPending', stop_pending_activity: 'stopPending', browser_not_found: 'browserMissing', browser_open_failed: 'browserMissing'};
  const error = (failed || localError || view?.error) && <div className="notice error" role="alert"><AlertCircle size={18}/><span>{view?.error && actionErrors[view.error] ? t(actionErrors[view.error]) : view?.errorText || t('genericError')}</span></div>;
  const titlebar = !nativeFrame && <header className="titlebar"><span><img src={brand} alt=""/>SOFT Tracking</span><div>{isPreview && <small>{t('preview')}</small>}{iconButton(t('minimize'), <Minus size={16}/>, () => void window.tracking?.window('minimize'))}{iconButton(t('close'), <X size={16}/>, () => void window.tracking?.window('close'))}</div></header>;

  if (!view) return <div className="app-shell">{titlebar}<main className="loading"><Spinner/><p>{failed ? t('genericError') : t('loading')}</p></main></div>;
  if (view.mode === 'installer') return <div className="app-shell installer-shell">{titlebar}
    {page === 'help' ? <div className="installer-guide"><header className="guide-header"><Button isIconOnly variant="ghost" aria-label={t('back')} onPress={() => setPage('connection')}><ArrowLeft size={19}/></Button><h1>{t('help')}</h1></header><Suspense fallback={<Spinner/>}><Guide language={language} busy={busy} onAction={act} extensionAvailable={view.phase === 'complete'}/></Suspense></div> : <main className="installer-main">
      <img src={brand} className="installer-brand" alt="SOFT Tracking"/>
      <h1>{t(view.phase === 'complete' ? 'installationComplete' : view.phase === 'failed' ? 'installFailed' : view.phase === 'installing' ? 'installing' : 'installTitle')}</h1>
      <p className="muted">{t('installSubtitle')}</p>
      <div className="install-status">
        {view.busy ? <><ProgressBar aria-label={t('installing')} isIndeterminate><ProgressBar.Track><ProgressBar.Fill/></ProgressBar.Track></ProgressBar><div className="install-progress-label"><Spinner size="sm"/><span>{t('installing')}</span></div></> : view.phase === 'complete' ? <div className="installed-symbol"><CheckCheck size={32}/></div> : null}
        {error}
        {!view.code && <TextField value={code} onChange={setCode} className="code-field"><Label>{t('companyCode')}</Label><Input autoComplete="off" spellCheck={false}/></TextField>}
        {view.phase === 'complete' ? <Button onPress={() => void act('launch')} className="wide-button">{t('openApp')}<ArrowRight size={18}/></Button> : !view.busy && <Button isDisabled={busy || !/^[a-f0-9]{32}$/.test(view.code || code)} onPress={() => void act('install', {code: view.code || code})} className="wide-button"><Download size={18}/>{t('install')}</Button>}
      </div>
      <div className="install-includes"><span><Check size={16}/>{t('files')}</span><span><Check size={16}/>{t('extension')}</span></div>
      <p className="install-safety"><ShieldCheck size={16}/>{t('keepData')}</p>
    </main>}
    <footer className="installer-footer">{languageControl}{iconButton(t('help'), <CircleHelp size={18}/>, () => setPage('help'))}</footer>
  </div>;

  const receipt = view.receipt || {}, browsers = view.browsers || [];
  const recent = Boolean(receipt.end_timestamp && Date.now()/1000 - receipt.end_timestamp < 180);
  const browserReady = browsers.some(row => row.connected && !row.error);
  const recording = view.collection === 'recording';
  const healthy = recording && recent && browserReady && !view.deliveryError;
  const statusText: Message = healthy ? 'active' : !recording ? 'stopped' : !browserReady ? 'needsAttention' : !receipt.hostname ? 'awaitingSession' : 'oldReceipt';
  return <div className="app-shell">{titlebar}<div className="workspace">
    <aside className="sidebar"><div className="brand"><img src={brand} alt=""/><strong>SOFT<br/>Tracking</strong></div>
      <nav>{([['connection', Link], ['browsers', Globe2], ['settings', Settings2]] as const).map(([id, Icon]) => <Button key={id} variant="ghost" aria-current={page === id ? 'page' : undefined} onPress={() => setPage(id)}><Icon size={19}/><span>{t(id)}</span></Button>)}</nav>
      <Button className="help-button" variant="ghost" aria-current={page === 'help' ? 'page' : undefined} onPress={() => setPage('help')}><CircleHelp size={18}/><span>{t('help')}</span></Button>
    </aside>
    <div className="content-column"><header className="page-header"><h1>{t(page as Message)}</h1><div className="header-controls">{languageControl}</div></header>
    <ScrollShadow className="content-scroll" hideScrollBar>
      <main className="page-content">{error}
      {page === 'connection' && (!view.enrolled ? <div className="activation"><div className="activation-icon"><Link size={26}/></div><h2>{t('enterCode')}</h2><p className="muted">{t('activationInfo')}</p>
        {view.company && <div className="company-line"><Building2 size={18}/>{view.company}</div>}
        <form onSubmit={event => {event.preventDefault(); void act('enroll', {code: view.code || code, key: key.trim()});}}>
          {!view.code && <TextField value={code} onChange={setCode}><Label>{t('companyCode')}</Label><Input autoComplete="off" spellCheck={false}/></TextField>}
          <TextField value={key} onChange={setKey} isRequired><Label>{t('employeeKey')}</Label><Input autoFocus autoComplete="off" spellCheck={false} className="key-input"/></TextField>
          <Button type="submit" isDisabled={busy || !/^[a-f0-9]{64}$/.test(key.trim()) || !/^[a-f0-9]{32}$/.test(view.code || code)}>{busy ? <Spinner size="sm"/> : <ArrowRight size={18}/>} {t('activate')}</Button>
        </form><div className="privacy-notice"><ShieldCheck size={20}/><p>{t('scopeNotice')}</p></div>
      </div> : <>
        <div className={'connection-status ' + (healthy ? 'healthy' : 'attention')}><div className="status-icon">{healthy ? <CheckCheck size={27}/> : <Activity size={27}/>}</div><div><h2>{t(statusText)}</h2><p>{view.company} <span className="dot-separator">·</span> {view.employee}</p></div>{iconButton(t('check'), <RefreshCw size={18} className={busy ? 'spin' : ''}/>, () => void act('check'))}</div>
        <div className="identity-rows">{[
          {icon: Building2, name: t('company'), value: view.company, done: true},
          {icon: UserRound, name: t('employee'), value: view.employee, done: true},
          {icon: Globe2, name: t('browser'), value: browserReady ? browsers.filter(row => row.connected).map(row => row.family).join(', ') : t('waiting'), done: browserReady},
        ].map(row => <div className="identity-row" key={row.name}><row.icon size={19}/><span>{row.name}</span><strong>{row.value}</strong>{row.done ? <CheckCircle2 className="success" size={18}/> : <Clock3 className="muted" size={18}/>}</div>)}</div>
        <Button variant="ghost" className="switch-employee-button" isDisabled={busy} onPress={() => {setSwitching(!switching); setKey('');}}><UserRoundPen size={16}/>{t('switchEmployee')}</Button>
        {switching && <form className="employee-switch" onSubmit={event => {event.preventDefault(); void act('switch-employee', {key: key.trim()});}}><h3>{t('switchEmployee')}</h3><p className="muted">{t('switchNotice')}</p><TextField value={key} onChange={setKey} isRequired><Label>{t('employeeKey')}</Label><Input autoFocus autoComplete="off" spellCheck={false}/></TextField><div className="actions"><Button type="submit" isDisabled={busy || !/^[a-f0-9]{64}$/.test(key.trim())}><UserRoundPen size={16}/>{t('switchEmployee')}</Button><Button variant="ghost" isDisabled={busy} onPress={() => {setSwitching(false); setKey('');}}>{t('cancel')}</Button></div></form>}
        <section className="receipt-section"><h3>{t('confirmed')}</h3>{receipt.hostname ? <><div className="receipt-title"><Globe2 size={18}/><strong>{receipt.hostname}</strong><span>{Math.max(0, Math.round((receipt.end_timestamp || 0)-(receipt.timestamp || 0)))} {t('seconds')}</span></div><p className="muted time-range">{date(receipt.timestamp)} <ArrowRight size={13}/> {date(receipt.end_timestamp)}</p><div className="delivery-line"><ShieldCheck size={16}/><span>{t('delivered')}</span><time>{date(receipt.confirmed_at)}</time></div></> : <p className="empty-state">{t('awaitingSession')}</p>}</section>
        <div className="queue-row"><span><Clock3 size={17}/>{t('pending')}<strong>{view.pending}</strong></span><span><AlertCircle size={17}/>{t('rejected')}<strong>{view.rejected}</strong></span></div>
        {view.deliveryError && <div className="notice warning"><AlertCircle size={18}/><p>{t('offline')}</p></div>}
        <div className="actions">{view.collection === 'paused_local' && <Button onPress={() => void act('resume')} isDisabled={busy}><Play size={17}/>{t('resume')}</Button>}<Button onPress={() => void act('check')} isDisabled={busy}><RefreshCw size={17}/>{t('check')}</Button><Button variant="ghost" isDisabled={!(view.domains || []).some(domain => /\.(kommo\.com|amocrm\.ru)$/.test(domain))} onPress={() => void act('open', {target: 'dashboard'})}>{t('dashboard')}<ExternalLink size={16}/></Button></div>
        {view.message && <p className="action-message" role="status">{t(view.message === 'employee_changed' ? 'employeeChanged' : view.message === 'connected' ? 'connected' : 'checkingDone')}</p>}
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
