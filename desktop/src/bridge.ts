import type {Language} from './locale';
export type View = {
  mode: 'desktop' | 'installer'; language: Language; theme: 'system'; version: string; code: string;
  busy: boolean; error: string; errorText?: string; message: string; phase: string; enrolled: boolean;
  company?: string; employee?: string; collection?: string; pending?: number; rejected?: number;
  deliveryError?: boolean; receipt?: {hostname?: string; timestamp?: number; end_timestamp?: number; confirmed_at?: number};
  browsers?: {family: string; version: string; connected: boolean; error?: string; last_seen: number}[];
  domains?: string[]; programs?: string[]; policy?: Record<string, boolean>; update?: string;
  admin?: {admin_required: boolean; force_kill_protected: boolean; authorization?: {available: boolean; mechanism: string}; autostart?: {registered: boolean | null; effective: string}};
};
type Bridge = {nativeFrame: boolean; invoke: (action: string, input?: Record<string, unknown>) => Promise<View>; window: (action: string) => Promise<void>};
declare global {interface Window {tracking?: Bridge}}
export const isPreview = !window.tracking && new URLSearchParams(location.search).get('preview') === '1';
export const nativeFrame = window.tracking?.nativeFrame ?? (isPreview && ['darwin', 'linux'].includes(new URLSearchParams(location.search).get('platform') || ''));
let preview: View = {
  mode: new URLSearchParams(location.search).get('mode') === 'installer' ? 'installer' : 'desktop',
  language: (new URLSearchParams(location.search).get('lang') || 'ru') as Language,
  theme: 'system',
  version: '3.2.0', code: 'a'.repeat(32), busy: false, error: '', message: '', phase: 'waiting',
  enrolled: new URLSearchParams(location.search).get('enroll') !== '1',
  company: 'Demo company', employee: 'Demo employee', collection: new URLSearchParams(location.search).get('paused') === '1' ? 'paused_local' : 'recording', pending: 0, rejected: 0,
  receipt: {hostname: 'demo.kommo.com', timestamp: Date.now()/1000-90, end_timestamp: Date.now()/1000-15, confirmed_at: Date.now()/1000-5},
  browsers: [{family: 'Chrome', version: '3.2.0', connected: true, last_seen: Date.now()/1000}],
  domains: ['demo.kommo.com', 'docs.google.com', 'mail.google.com'], programs: ['EXCEL.EXE', 'WINWORD.EXE'],
  policy: {tracking: true, interactions: true, field_values: true, app_inventory: true}, update: 'active',
  admin: {admin_required: true, force_kill_protected: false, autostart: {registered: true, effective: 'enabled'}},
};
if (!['ru', 'en', 'cs', 'uz'].includes(preview.language)) preview.language = 'en';
export async function invoke(action: string, input: Record<string, unknown> = {}): Promise<View> {
  if (window.tracking) return window.tracking.invoke(action, input);
  if (!isPreview) throw Error('desktop_bridge_unavailable');
  if (action === 'resume') {
    if (Object.keys(input).length || preview.mode !== 'desktop' || !preview.enrolled || preview.busy || preview.collection !== 'paused_local') throw Error('invalid_request');
    preview = {...preview, collection: 'recording'};
  }
  if (action === 'preferences') {
    if (Object.keys(input).some(key => key !== 'language') || !['en', 'ru', 'cs', 'uz'].includes(String(input.language))) throw Error('invalid_request');
    preview = {...preview, language: input.language as Language};
  }
  if (action === 'switch-employee') {
    if (!/^[a-f0-9]{64}$/.test(String(input.key))) throw Error('invalid_request');
    preview = {...preview, employee: 'Demo colleague', message: 'employee_changed', receipt: {}};
  }
  if (action === 'stop-agent') preview = {...preview, message: 'stop_cancelled'};
  if (action === 'install') {
    preview = {...preview, busy: true, phase: 'installing'};
    setTimeout(() => {preview = {...preview, busy: false, phase: 'complete'};}, 2000);
  }
  if (action === 'enroll') preview = {...preview, enrolled: true};
  if (action === 'check') preview = {...preview, message: 'check_complete'};
  return {...preview};
}
