import { AlertCircle, Check, LoaderCircle, Square } from 'lucide-react';
import { isActive, stageLabels, stages, statusLabels } from '../constants';
import type { Review } from '../types';

const stageAliases: Record<string, string> = {
  ingestion: 'ingest', static: 'static_scan', ai: 'ai_review',
  dependencies: 'dependency_scan', osv: 'dependency_scan', summarize: 'summary', completed: 'summary',
};

interface Props {
  review: Review;
  onCancel: () => void;
  cancelling: boolean;
  cancelError: string;
}

export default function ProgressPanel({ review, onCancel, cancelling, cancelError }: Props) {
  const active = isActive(review.status);
  const normalizedStage = review.stage ? stageAliases[review.stage] || review.stage : '';
  const currentIndex = stages.indexOf(normalizedStage as (typeof stages)[number]);
  const progress = Math.max(0, Math.min(100, review.progress));

  return (
    <section className={`panel progress-panel ${active ? 'is-running' : ''}`} aria-labelledby="progress-title">
      <div className="progress-topline">
        <div>
          <div className="progress-title-row">
            {active && <LoaderCircle size={19} className="spin" />}
            <h2 id="progress-title">{active ? '审查正在进行' : statusLabels[review.status]}</h2>
          </div>
          <p>{review.stage_detail || (review.status === 'queued' ? '任务已进入队列，等待执行。' : '任务已停止更新。')}</p>
        </div>
        <strong className="progress-number">{Math.round(progress)}<small>%</small></strong>
      </div>
      <progress className="main-progress" max="100" value={progress} aria-label="审查任务进度">{progress}%</progress>
      <ol className="stage-list" aria-label="审查阶段">
        {stages.map((stage, index) => {
          const complete = review.status === 'completed' || index < currentIndex;
          const current = active && index === currentIndex;
          const stopped = !active && review.status !== 'completed' && index === currentIndex;
          return (
            <li key={stage} className={`${complete ? 'complete' : ''} ${current ? 'current' : ''} ${stopped ? 'stopped' : ''}`} aria-current={current ? 'step' : undefined}>
              <span className="stage-marker">{complete ? <Check size={14} /> : current ? <LoaderCircle size={14} className="spin" /> : index + 1}</span>
              <span>{stageLabels[stage]}</span>
            </li>
          );
        })}
      </ol>
      {review.error && <div className="inline-alert alert-error" role="alert"><AlertCircle size={19} /><div><strong>任务未能完整执行</strong><p>{review.error}</p></div></div>}
      {cancelError && <div className="inline-alert alert-error" role="alert"><AlertCircle size={18} />{cancelError}</div>}
      {active && (
        <div className="progress-actions">
          <p>页面会自动刷新；离开页面不会取消后端任务。</p>
          <button className="button button-danger-quiet" type="button" onClick={onCancel} disabled={cancelling}><Square size={15} />{cancelling ? '正在取消…' : '取消审查'}</button>
        </div>
      )}
    </section>
  );
}
