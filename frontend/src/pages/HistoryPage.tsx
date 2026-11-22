import { useCallback, useEffect, useState } from 'react';
import { ArrowRight, Clock3, FileArchive, FileCode2, Github, Plus, RefreshCw } from 'lucide-react';
import { Link } from 'react-router-dom';
import { errorMessage, listReviews } from '../api';
import { formatDate, isActive } from '../constants';
import { StatusBadge } from '../components/Badges';
import { EmptyHistory, ErrorState, LoadingState } from '../components/PageState';
import type { Review } from '../types';

export default function HistoryPage() {
  const [reviews, setReviews] = useState<Review[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const data = await listReviews();
      setReviews(data);
      setError('');
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      if (!silent) setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  useEffect(() => {
    if (!reviews.some((review) => isActive(review.status))) return;
    const timer = window.setInterval(() => { void load(true); }, 2000);
    return () => window.clearInterval(timer);
  }, [reviews, load]);

  return (
    <div className="history-page">
      <section className="page-heading heading-with-action">
        <div><span className="eyebrow"><Clock3 size={15} />本地审查记录</span><h1>审查历史</h1><p>回到任意任务，复核代码证据、覆盖范围与依赖风险。</p></div>
        <Link className="button button-primary" to="/"><Plus size={17} />新建审查</Link>
      </section>

      {loading ? <LoadingState label="正在读取审查历史" /> : error && reviews.length === 0 ? <ErrorState message={error} onRetry={() => void load()} /> : reviews.length === 0 ? <EmptyHistory /> : (
        <>
          {error && <div className="inline-alert alert-error" role="alert">{error}<button type="button" onClick={() => void load()}>重试</button></div>}
          <div className="history-toolbar"><span>共 {reviews.length} 次审查</span><button type="button" className="text-button" onClick={() => void load()}><RefreshCw size={15} />刷新列表</button></div>
          <div className="history-list">
            {reviews.map((review) => {
              const eligible = review.coverage?.eligible_files || review.coverage?.total_files || 0;
              const analyzed = review.coverage?.analyzed_files || 0;
              const percentage = eligible > 0 ? Math.round((analyzed / eligible) * 100) : null;
              return (
                <Link to={`/reviews/${review.id}`} className="history-card" key={review.id}>
                  <div className={`source-icon source-${review.source_type}`}>{review.source_type === 'github' ? <Github size={22} /> : <FileArchive size={22} />}</div>
                  <div className="history-main">
                    <div className="history-title"><h2>{review.source_name}</h2><StatusBadge status={review.status} /></div>
                    <div className="history-meta"><span>{review.source_type === 'github' ? 'GitHub 仓库' : 'ZIP 源码包'}</span><span>创建于 {formatDate(review.created_at)}</span></div>
                    {isActive(review.status) && <div className="history-progress"><div><span>{review.stage_detail || '任务正在处理中'}</span><strong>{Math.round(review.progress)}%</strong></div><progress max="100" value={review.progress} aria-label={`${review.source_name} 审查进度`} /></div>}
                  </div>
                  <div className="history-stats">
                    {review.findings ? <span><FileCode2 size={15} />{review.findings.length} 项发现</span> : <span>查看完整结果</span>}
                    {percentage !== null && <span>AI 覆盖 {percentage}%</span>}
                  </div>
                  <ArrowRight className="history-arrow" size={19} />
                </Link>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
