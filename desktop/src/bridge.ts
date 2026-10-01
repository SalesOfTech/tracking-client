import type {Language} from './locale';
import {version as previewVersion} from '../package.json';
export const browserPages = Object.freeze({
  Chrome: 'chrome://extensions', Edge: 'edge://extensions', Yandex: 'browser://extensions',
  Opera: 'opera://extensions', Brave: 'brave://extensions', Vivaldi: 'vivaldi://extensions',
  Chromium: 'chrome://extensions', Firefox: 'about:addons',
});
export const browserPage = (family: string) => Object.hasOwn(browserPages, family) ? browserPages[family as keyof typeof browserPages] : undefined;
export let previewClipboard = '';
export type View = {
  mode: 'desktop' | 'installer'; language: Language; theme: 'system'; version: string; code: string;
  busy: boolean; error: string; errorText?: string; errorCode?: string; message: string; phase: string; enrolled: boolean;
  company?: string; employee?: string; collection?: string; pending?: number; rejected?: number;
  deliveryError?: boolean; receipt?: {hostname?: string; timestamp?: number; end_timestamp?: number; confirmed_at?: number};
  browsers?: {family: string; version: string; connected: boolean; error?: string; last_seen: number}[];
  domains?: string[]; programs?: string[]; policy?: Record<string, boolean>; update?: string;
  updateCheckedAt?: number; updateAvailable?: boolean; updateChecking?: boolean;
  canManage?: boolean; canUninstall?: boolean; uninstalling?: boolean;
  admin?: {admin_required: boolean; force_kill_protected: boolean; authorization?: {available: boolean; mechanism: string}; autostart?: {registered: boolean | null; effective: string}};
};
export type CopyAcknowledgement = {copied: true};
type Invoke = {
  (action: 'copy-browser-page', input: Record<string, unknown>): Promise<CopyAcknowledgement>;
  (action: string, input?: Record<string, unknown>): Promise<View>;
};
type Bridge = {nativeFrame: boolean; invoke: Invoke; window: (action: string) => Promise<void>};
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
  updateAvailable: params.get('mode') !== 'installer' && params.get('enroll') !== '1' && params.get('updateAvailable') !== '0',
  updateChecking: params.has('updateChecking') ? params.get('updateChecking') === '1' : params.get('update') === 'checking',
  updateCheckedAt: params.get('updateCheckedAt') === 'none' ? undefined : now - 60,
  canManage: false, canUninstall: false,
  admin: {admin_required: false, force_kill_protected: false, autostart: {registered: true, effective: 'enabled'}},
};
if (!['ru', 'en', 'cs', 'uz'].includes(preview.language)) preview.language = 'en';
if (['checking', 'active', 'installed', 'downloading', 'rolled_back', 'error', 'registration'].includes(params.get('update') || '')) preview.update = params.get('update')!;
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
export function invoke(action: 'copy-browser-page', input: Record<string, unknown>): Promise<CopyAcknowledgement>;
export function invoke(action: string, input?: Record<string, unknown>): Promise<View>;
export async function invoke(action: string, input: Record<string, unknown> = {}): Promise<View | CopyAcknowledgement> {
  if (window.tracking) return window.tracking.invoke(action, input);
  if (!isPreview) throw Error('desktop_bridge_unavailable');
  if (action === 'copy-browser-page') {
    if (Object.keys(input).length !== 1 || typeof input.browser !== 'string' || !browserPage(input.browser)) throw Error('invalid_request');
    previewClipboard = browserPage(input.browser)!;
    return {copied: true};
  }
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
  if (action === 'stop-agent' || action === 'uninstall-agent') throw Error('invalid_request');
  if (action === 'install') {
    preview = {...preview, busy: true, phase: 'installing'};
    setTimeout(() => {preview = {...preview, busy: false, phase: 'complete'};}, 2000);
  }
  if (action === 'enroll') preview = {...preview, enrolled: true, updateAvailable: preview.mode === 'desktop' && params.get('updateAvailable') !== '0'};
  if (action === 'check-update') {
    if (Object.keys(input).length || preview.mode !== 'desktop' || !preview.enrolled || !preview.updateAvailable || preview.updateChecking || preview.update === 'downloading') throw Error('invalid_request');
    preview = {...preview, updateChecking: true};
    await new Promise(resolve => setTimeout(resolve, 350));
    preview = {...preview, update: params.get('updateResult') === 'error' ? 'error' : 'active', updateChecking: false, updateCheckedAt: Date.now() / 1000};
  }
  if (action === 'check') {
    preview = {...preview, busy: true};
    await new Promise(resolve => setTimeout(resolve, 350));
    preview = {...preview, busy: false, message: preview.deliveryError ? 'check_offline' : preview.browsers?.some(row => row.connected && !row.error) ? 'check_server_ok' : 'check_browser_missing'};
  }
  return {...preview};
}
