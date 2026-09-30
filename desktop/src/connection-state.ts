import type {View} from './bridge';
import type {Message} from './locale';

export type ReceiptState = 'missing' | 'invalid' | 'stale' | 'recent';
type ConnectionState = {
  tone: 'success' | 'danger' | 'accent';
  title: Message;
  detail: Message;
  action: 'check' | 'browsers' | 'resume' | 'retry';
  receipt: ReceiptState;
};

export const viewIsBusy = (view: Pick<View, 'busy' | 'phase'>): boolean => view.busy || ['detecting', 'migrating', 'installing'].includes(view.phase);

export function receiptState(receipt: View['receipt'], now = Date.now() / 1000): ReceiptState {
  if (!receipt || Object.keys(receipt).length === 0) return 'missing';
  const {hostname, timestamp: start, end_timestamp: end, confirmed_at: confirmed} = receipt;
  const validTime = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value) && value > 0 && value <= now + 30;
  if (!Number.isFinite(now) || now <= 0 || typeof hostname !== 'string' || !hostname || /[\s/\\?#@]/.test(hostname)
    || !validTime(start) || !validTime(end) || !validTime(confirmed) || end < start) return 'invalid';
  // Confirmation uses integer seconds; a session end may include fractional seconds.
  if (confirmed + 1 < end) return 'invalid';
  return now - end >= 180 ? 'stale' : 'recent';
}

export function connectionState(view: View, busy = false, failed = false, now = Date.now() / 1000): ConnectionState {
  const receipt = receiptState(view.receipt, now);
  const state = (tone: ConnectionState['tone'], title: Message, detail: Message, action: ConnectionState['action'] = 'check'): ConnectionState => ({tone, title, detail, action, receipt});
  if (busy || viewIsBusy(view)) return state('accent', 'connectionChecking', 'connectionCheckingDetail');
  if (failed || view.error || view.errorCode || view.errorText || view.phase === 'failed') return state('danger', 'connectionProblem', 'genericError');
  if (view.deliveryError || view.message === 'check_offline') return state('danger', 'connectionProblem', 'deliveryFailed');
  if (view.rejected) return state('danger', 'connectionProblem', 'rejectedDetail', 'retry');
  if (!view.enrolled) return state('danger', 'enterCode', 'activationInfo');
  if (view.collection !== 'recording') return state('danger', view.collection === 'paused_local' ? 'stopped' : 'collectionUnavailable', 'collectionStoppedDetail', view.collection === 'paused_local' ? 'resume' : 'check');
  if (view.browsers?.some(row => row.error)) return state('danger', 'connectionProblem', 'browserErrorDetail', 'browsers');
  if (!view.browsers?.some(row => row.connected === true && !row.error)) return state('danger', 'browserDisconnected', 'browserDisconnectedDetail', 'browsers');
  if (view.message === 'check_pending') return state('danger', 'connectionProblem', 'check_pending');
  if (view.message === 'legacy_cleanup_warning') return state('danger', 'connectionProblem', 'legacy_cleanup_warning');
  if (receipt === 'missing') return state('danger', 'awaitingSession', 'awaitingSessionDetail');
  if (receipt === 'invalid') return state('danger', 'invalidReceipt', 'invalidReceiptDetail');
  if (receipt === 'stale') return state('danger', 'oldReceipt', 'oldReceiptDetail');
  return state('success', 'connectionHealthy', 'connectionHealthyDetail');
}
