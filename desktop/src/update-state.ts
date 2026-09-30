import type {View} from './bridge';
import type {Message} from './locale';

export function updateState(view: View, requestPending = false, requestFailed = false, now = Date.now() / 1000) {
  const available = view.mode === 'desktop' && view.enrolled && view.updateAvailable === true;
  const busy = available && (requestPending || (view.updateChecking ?? view.update === 'checking') || view.update === 'downloading');
  const labels: Record<string, Message> = {active: 'installed', installed: 'installed', downloading: 'downloading', rolled_back: 'rollback', error: 'updateError'};
  const label: Message = !view.enrolled || (!available && view.update === 'registration') ? 'updateRegistration'
    : !available ? 'updateUnavailable'
    : view.update === 'downloading' ? 'downloading'
    : busy ? 'checking'
    : requestFailed ? 'updateCheckFailed'
    : labels[view.update || ''] || 'updateNotChecked';
  const checkedAt = typeof view.updateCheckedAt === 'number' && Number.isFinite(view.updateCheckedAt) && view.updateCheckedAt > 0 && view.updateCheckedAt <= now + 30 ? view.updateCheckedAt : undefined;
  return {busy, disabled: !available || busy, label, checkedAt, failed: label === 'updateError' || label === 'updateCheckFailed'};
}
