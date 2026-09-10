import {useEffect, useRef, useState, type ReactNode} from 'react';
import {createRoot} from 'react-dom/client';
import {Button, Input, Label, ListBox, ProgressBar, ScrollShadow, Select, Spinner, TextField, Tooltip} from '@heroui/react';
import {Activity, ArrowRight, Check, CheckCheck, CheckCircle2, CircleHelp, Clock3, Download, ExternalLink, FolderOpen, Globe2, Link, Minus, Monitor, Moon, Play, RefreshCw, Settings2, ShieldCheck, Sun, X, AlertCircle, Building2, UserRound, ChevronRight} from 'lucide-react';
import brand from '../../extension/icons/icon128.png';
import {invoke, isPreview, type Theme, type View} from './bridge';
import {languages, text, type Language, type Message} from './locale';
import './styles.css';

function App() {
  const [view, setView] = useState<View | null>(null), [failed, setFailed] = useState(false);
  const [page, setPage] = useState('connection'), [key, setKey] = useState(''), [code, setCode] = useState('');
  const [localBusy, setLocalBusy] = useState(false), [localError, setLocalError] = useState(false);
  const [isDark, setIsDark] = useState(matchMedia('(prefers-color-scheme: dark)').matches);
  const started = useRef(false), mounted = useRef(true);
  const language = view?.language || 'en', t = (id: Message) => text(language, id);
  const theme = view?.theme || 'system';
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
      const dark = theme === 'dark' || (theme === 'system' && media.matches);
      document.documentElement.classList.toggle('dark', dark);
      document.documentElement.dataset.theme = dark ? 'dark' : 'light';
      document.documentElement.style.colorScheme = dark ? 'dark' : 'light';
      setIsDark(dark);
    };
    update(); media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, [theme]);
  useEffect(() => {document.documentElement.lang = language;}, [language]);
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
    <Select.Trigger><Globe2 size={15}/><Select.Value/><Select.Indicator/></Select.Trigger>
    <Select.Popover><ListBox>{Object.entries(languages).map(([id, name]) => <ListBox.Item key={id} id={id} textValue={name}><Label>{name}</Label><ListBox.ItemIndicator/></ListBox.Item>)}</ListBox></Select.Popover>
  </Select>;
  const themeControl = <div className="theme-control" role="group" aria-label={t('theme')}>{([
    ['system', <Monitor size={17}/>], ['light', <Sun size={17}/>], ['dark', <Moon size={17}/>],
  ] as [Theme, ReactNode][]).map(([id, icon]) => <span key={id}>{iconButton(t(id), icon, () => void act('preferences', {theme: id}), theme === id)}</span>)}</div>;
  const error = (failed || localError || view?.error) && <div className="notice error" role="alert"><AlertCircle size={18}/><span>{view?.errorText || t('genericError')}</span></div>;
  const titlebar = <header className="titlebar"><span><img src={brand} alt=""/>SOFT Tracking</span><div>{isPreview && <small>{t('preview')}</small>}{iconButton(t('minimize'), <Minus size={16}/>, () => void window.tracking?.window('minimize'))}{iconButton(t('close'), <X size={16}/>, () => void window.tracking?.window('close'))}</div></header>;

  if (!view) return <div className="app-shell">{titlebar}<main className="loading"><Spinner/><p>{failed ? t('genericError') : t('loading')}</p></main></div>;
  if (view.mode === 'installer') return <div className="app-shell installer-shell">{titlebar}
    <main className="installer-main">
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
    </main>
    <footer className="installer-footer">{languageControl}{themeControl}{iconButton(t('help'), <CircleHelp size={18}/>, () => void act('open', {target: 'guide'}))}</footer>
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
      <Button className="help-button" variant="ghost" onPress={() => void act('open', {target: 'guide'})}><CircleHelp size={18}/><span>{t('help')}</span></Button>
    </aside>
    <div className="content-column"><header className="page-header"><h1>{t(page as Message)}</h1><div className="header-controls">{languageControl}{iconButton(t(isDark ? 'light' : 'dark'), isDark ? <Sun size={19}/> : <Moon size={19}/>, () => void act('preferences', {theme: isDark ? 'light' : 'dark'}))}</div></header>
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
        <section className="receipt-section"><h3>{t('confirmed')}</h3>{receipt.hostname ? <><div className="receipt-title"><Globe2 size={18}/><strong>{receipt.hostname}</strong><span>{Math.max(0, Math.round((receipt.end_timestamp || 0)-(receipt.timestamp || 0)))} {t('seconds')}</span></div><p className="muted time-range">{date(receipt.timestamp)} <ArrowRight size={13}/> {date(receipt.end_timestamp)}</p><div className="delivery-line"><ShieldCheck size={16}/><span>{t('delivered')}</span><time>{date(receipt.confirmed_at)}</time></div></> : <p className="empty-state">{t('awaitingSession')}</p>}</section>
        <div className="queue-row"><span><Clock3 size={17}/>{t('pending')}<strong>{view.pending}</strong></span><span><AlertCircle size={17}/>{t('rejected')}<strong>{view.rejected}</strong></span></div>
        {view.deliveryError && <div className="notice warning"><AlertCircle size={18}/><p>{t('offline')}</p></div>}
        <div className="actions">{view.collection === 'paused_local' && <Button onPress={() => void act('resume')} isDisabled={busy}><Play size={17}/>{t('resume')}</Button>}<Button onPress={() => void act('check')} isDisabled={busy}><RefreshCw size={17}/>{t('check')}</Button><Button variant="ghost" isDisabled={!(view.domains || []).some(domain => /\.(kommo\.com|amocrm\.ru)$/.test(domain))} onPress={() => void act('open', {target: 'dashboard'})}>{t('dashboard')}<ExternalLink size={16}/></Button></div>
        {view.message && <p className="action-message" role="status">{t(view.message === 'connected' ? 'connected' : 'checkingDone')}</p>}
      </>)}
      {page === 'browsers' && <><h2>{t('browsers')}</h2><p className="muted section-intro">{t('browserNotice')}</p><div className="browser-list">{browsers.length ? browsers.map((row, index) => <div key={index} className="browser-row"><Globe2 size={25}/><div><strong>{row.family}</strong><p className="muted">{row.version} · {t('lastContact')}: {date(row.last_seen)}</p></div><span className={row.connected && !row.error ? 'success' : 'warning-text'}>{t(row.connected && !row.error ? 'connected' : 'waiting')}</span></div>) : <div className="empty-state">{t('noBrowsers')}</div>}</div><div className="actions"><Button onPress={() => void act('repair')} isDisabled={busy}><RefreshCw size={17}/>{t('repair')}</Button><Button variant="secondary" onPress={() => void act('open', {target: 'extension'})}><FolderOpen size={17}/>{t('extensionFolder')}</Button><Button variant="ghost" onPress={() => void act('open', {target: 'guide'})}>{t('help')}<ChevronRight size={16}/></Button></div></>}
      {page === 'settings' && <><div className="preference-row"><div><h3>{t('theme')}</h3></div>{themeControl}</div><section className="settings-section"><h3>{t('scope')}</h3><p className="muted section-intro">{t('scopeNotice')}</p>{([['webTime', 'tracking'], ['clicks', 'interactions'], ['fields', 'field_values'], ['inventory', 'app_inventory']] as [Message, string][]).map(([label, flag]) => <div key={flag} className="setting-row"><span>{t(label)}</span><span className={view.policy?.[flag] ? 'success' : 'muted'}>{t(view.policy?.[flag] ? 'on' : 'off')}</span></div>)}</section><section className="settings-section"><h3>{t('domains')}</h3>{view.domains?.length ? <ul className="scope-list">{view.domains.map(domain => <li key={domain}><Globe2 size={15}/>{domain}</li>)}</ul> : <p className="muted">{t('empty')}</p>}<h3>{t('programs')}</h3>{view.programs?.length ? <ul className="scope-list">{view.programs.map(program => <li key={program}><Monitor size={15}/>{program}</li>)}</ul> : <p className="muted">{t('empty')}</p>}</section><Button variant="secondary" onPress={() => void act('retry')} isDisabled={busy}><RefreshCw size={17}/>{t('retry')}</Button></>}
      </main>
    </ScrollShadow><footer className="status-footer"><span><RefreshCw size={14}/>{t('update')}<span className="dot-separator">·</span>{t(({active: 'installed', installed: 'installed', downloading: 'downloading', error: 'updateError', rolled_back: 'rollback', registration: 'waiting'} as Record<string, Message>)[view.update || ''] || 'checking')}</span><span>{view.version}</span></footer></div>
  </div></div>;
}
createRoot(document.getElementById('root')!).render(<App/>);
