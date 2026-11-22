import { ExternalLink, PackageSearch, ShieldAlert } from 'lucide-react';
import type { Vulnerability } from '../types';
import { SeverityBadge } from './Badges';

function safeReference(value: string): boolean {
  try {
    return ['http:', 'https:'].includes(new URL(value).protocol);
  } catch {
    return false;
  }
}

export default function VulnerabilitiesPanel({ vulnerabilities, dependencyStatus }: { vulnerabilities: Vulnerability[]; dependencyStatus?: string }) {
  const unavailable = ['failed', 'unavailable', 'error'].includes((dependencyStatus || '').toLowerCase());
  return (
    <section className="results-section vulnerability-section" aria-labelledby="vulnerability-title">
      <div className="result-section-heading">
        <div><span className="section-icon vulnerability-icon"><ShieldAlert size={20} /></span><div><h2 id="vulnerability-title">依赖漏洞</h2><p>基于锁定依赖版本独立查询 OSV，不与代码发现混合计数。</p></div></div>
        <span className="count-pill">{vulnerabilities.length} 项</span>
      </div>
      {dependencyStatus && <div className={`dependency-status ${unavailable ? 'dependency-unavailable' : ''}`}><PackageSearch size={17} /><span>依赖扫描状态：<strong>{unavailable ? '不可用' : dependencyStatus === 'completed' ? '已完成' : dependencyStatus}</strong></span></div>}
      {vulnerabilities.length === 0 ? (
        <div className="result-empty compact-empty"><PackageSearch size={26} /><h3>{unavailable ? '依赖扫描结果不可用' : '未发现已知依赖漏洞'}</h3><p>{unavailable ? '请查看任务警告；其余代码审查结果仍然有效。' : '仅覆盖可解析且版本已锁定的依赖，不代表不存在其他漏洞。'}</p></div>
      ) : (
        <div className="vulnerability-list">
          {vulnerabilities.map((vulnerability) => (
            <article className="vulnerability-card" key={`${vulnerability.package_name}-${vulnerability.vulnerability_id}`}>
              <div className="vulnerability-heading"><div><code>{vulnerability.package_name}</code><span>v{vulnerability.package_version}</span></div><SeverityBadge severity={vulnerability.severity} /></div>
              <div className="vulnerability-meta"><span>{vulnerability.ecosystem}</span><strong>{vulnerability.vulnerability_id}</strong></div>
              <p>{vulnerability.summary}</p>
              {vulnerability.references.length > 0 && <div className="references"><strong>参考链接</strong><div>{vulnerability.references.filter(safeReference).map((reference, index) => <a href={reference} target="_blank" rel="noreferrer noopener" key={reference}>参考 {index + 1}<ExternalLink size={13} /><span className="sr-only">（新窗口打开）</span></a>)}</div></div>}
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
