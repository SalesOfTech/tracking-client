import type {Language} from './locale';
import {version as previewVersion} from '../package.json';
export type View = {
  mode: 'desktop' | 'installer'; language: Language; theme: 'system'; version: string; code: string;
  busy: boolean; error: string; errorText?: string; errorCode?: string; message: string; phase: string; enrolled: boolean;
  company?: string; employee?: string; collection?: string; pending?: number; rejected?: number;
  deliveryError?: boolean; receipt?: {hostname?: string; timestamp?: number; end_timestamp?: number; confirmed_at?: number};
  browsers?: {family: string; version: string; connected: boolean; error?: string; last_seen: number}[];
  domains?: string[]; programs?: string[]; policy?: Record<string, boolean>; update?: string;
  admin?: {admin_required: boolean; force_kill_protected: boolean; authorization?: {available: boolean; mechanism: string}; autostart?: {registered: boolean | null; effective: string}};
};
type Bridge = {nativeFrame: boolean; invoke: (action: string, input?: Record<string, unknown>) => Promise<View>; window: (action: string) => Promise<void>};
declare global {interface Window {tracking?: Bridge}}
const params = new URLSearchParams(location.search);
export const isPreview = !window.tracking && params.get('preview') === '1';
export const nativeFrame = window.tracking?.nativeFrame ?? (isPreview && ['darwin', 'linux'].includes(params.get('platform') || ''));
const now = Date.now() / 1000;
const phase = params.get('phase') || 'waiting';
let preview: View = {
  mode: params.get('mode') === 'installer' ? 'installer' : 'desktop',
  language: (params.get('lang') || 'ru') as Language,
  theme: 'system',
  version: previewVersion, code: params.get('noCode') === '1' ? '' : 'a'.repeat(32), busy: false, error: '', message: '', phase: 'waiting',
  enrolled: params.get('enroll') !== '1',
  company: 'Demo company', employee: 'Demo employee', collection: params.get('paused') === '1' ? 'paused_local' : 'recording', pending: 0, rejected: 0,
  receipt: {hostname: 'demo.kommo.com', timestamp: now-90, end_timestamp: now-15, confirmed_at: now-5},
  browsers: [{family: 'Chrome', version: '3.2.0', connected: true, last_seen: now}],
  domains: ['demo.kommo.com', 'docs.google.com', 'mail.google.com'], programs: ['EXCEL.EXE', 'WINWORD.EXE'],
  policy: {tracking: true, interactions: true, field_values: true, app_inventory: true}, update: 'active',
  admin: {admin_required: true, force_kill_protected: false, autostart: {registered: true, effective: 'enabled'}},
};
if (!['ru', 'en', 'cs', 'uz'].includes(preview.language)) preview.language = 'en';
if (preview.mode === 'installer') {
  if (['waiting', 'detecting', 'migrating', 'installing', 'complete', 'failed'].includes(phase)) preview.phase = phase;
  preview.busy = ['detecting', 'migrating', 'installing'].includes(preview.phase);
  if (preview.phase === 'failed') preview.error = 'setup_migration_failed';
} else {
  switch (params.get('state')) {
    case 'busy': preview.busy = true; break;
    case 'browser-missing': preview.browsers = []; break;
    case 'browser-error': preview.browsers![0].error = 'storage_unavailable'; break;
    case 'failed': preview.deliveryError = true; preview.errorCode = 'ST-DEMO-001'; break;
    case 'delivery-error': preview.deliveryError = true; break;
    case 'rejected': preview.rejected = 2; break;
    case 'awaiting-session': preview.receipt = {}; break;
    case 'invalid-receipt': preview.receipt = {...preview.receipt, confirmed_at: undefined}; break;
    case 'stale': preview.receipt = {...preview.receipt, timestamp: now-600, end_timestamp: now-300}; break;
    case 'paused': preview.collection = 'paused_local'; break;
  }
}
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
  if (action === 'check') {
    preview = {...preview, busy: true};
    await new Promise(resolve => setTimeout(resolve, 350));
    preview = {...preview, busy: false, message: preview.deliveryError ? 'check_offline' : preview.browsers?.some(row => row.connected && !row.error) ? 'check_server_ok' : 'check_browser_missing'};
  }
  return {...preview};
}
