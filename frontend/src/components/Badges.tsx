import { AlertCircle, CheckCircle2, CircleDashed, LoaderCircle, OctagonX, PauseCircle } from 'lucide-react';
import { severityLabels, statusLabels } from '../constants';
import type { ReviewStatus, Severity } from '../types';

export function StatusBadge({ status }: { status: ReviewStatus }) {
  const icons = {
    queued: CircleDashed,
    running: LoaderCircle,
    completed: CheckCircle2,
    failed: OctagonX,
    cancelled: PauseCircle,
    interrupted: AlertCircle,
  };
  const Icon = icons[status];
  return <span className={`status-badge status-${status}`}><Icon size={14} className={status === 'running' ? 'spin' : ''} />{statusLabels[status]}</span>;
}

export function SeverityBadge({ severity }: { severity: Severity | string }) {
  const normalized = severity.toLowerCase() as Severity;
  const label = severityLabels[normalized] || severity.toUpperCase();
  return <span className={`severity-badge severity-${normalized}`}>{label}</span>;
}
