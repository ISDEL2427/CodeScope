import { AlertCircle, Inbox, LoaderCircle, RefreshCw } from 'lucide-react';
import { Link } from 'react-router-dom';

export function LoadingState({ label = '正在加载审查数据' }: { label?: string }) {
  return <div className="page-state" role="status"><LoaderCircle className="spin" size={28} /><p>{label}</p></div>;
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="page-state error-state" role="alert">
      <AlertCircle size={30} />
      <h2>暂时无法加载</h2>
      <p>{message}</p>
      {onRetry && <button className="button button-secondary" type="button" onClick={onRetry}><RefreshCw size={16} />重新加载</button>}
    </div>
  );
}

export function EmptyHistory() {
  return (
    <div className="page-state empty-state">
      <span className="empty-icon"><Inbox size={30} /></span>
      <h2>还没有审查记录</h2>
      <p>从一个公共 GitHub 仓库或 ZIP 源码包开始第一次审查。</p>
      <Link className="button button-primary" to="/">新建审查</Link>
    </div>
  );
}
