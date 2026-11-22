import { AlertTriangle, CheckCircle2, GitCompare, History, ShieldAlert } from 'lucide-react';
import { formatDate } from '../constants';
import type { BaselineResponse, Finding } from '../types';
import { SeverityBadge } from './Badges';

const GROUPS = [
  { key: 'new', title: '本次新增', empty: '本次没有新增代码发现' },
  { key: 'resolved', title: '已修复或未复现', empty: '没有发现消失的风险' },
  { key: 'persisting', title: '仍然存在', empty: '没有延续的风险' },
] as const;

type GroupKey = (typeof GROUPS)[number]['key'];

function groupFindings(data: NonNullable<BaselineResponse['baseline']>, key: GroupKey): Finding[] {
  if (key === 'new') return data.new_findings;
  if (key === 'resolved') return data.resolved_findings;
  return data.persisting_findings;
}

export default function BaselinePanel({ data }: { data: BaselineResponse | null }) {
  const baseline = data?.baseline;

  return (
    <section className="results-section baseline-section" aria-labelledby="baseline-title">
      <div className="result-section-heading">
        <div>
          <span className="section-icon baseline-icon"><GitCompare size={20} /></span>
          <div>
            <h2 id="baseline-title">与上次体检对比</h2>
            <p>同一目标的相邻两次审查；按来源、类别、文件与标题匹配，行号变化不影响判断。</p>
          </div>
        </div>
        {baseline && <span className="count-pill">基线 {formatDate(baseline.baseline_created_at)}</span>}
      </div>

      {!data ? (
        <div className="result-empty compact-empty"><History size={27} /><h3>正在加载历史对比…</h3><p>读取同一目标上一次完成的审查结果。</p></div>
      ) : !baseline ? (
        <div className="result-empty compact-empty">
          <History size={27} />
          <h3>暂无可对比的历史审查</h3>
          <p>{data.message || '完成下一次体检后，这里会显示新增与已修复的风险。'}</p>
        </div>
      ) : (
        <>
          <div className="baseline-summary">
            <div className="baseline-stat baseline-new"><span><AlertTriangle size={16} />新增风险</span><strong>{baseline.new_findings.length}</strong><small>本次新出现</small></div>
            <div className="baseline-stat baseline-resolved"><span><CheckCircle2 size={16} />已修复</span><strong>{baseline.resolved_findings.length}</strong><small>上次存在，本次未复现</small></div>
            <div className="baseline-stat baseline-persisting"><span><GitCompare size={16} />仍存在</span><strong>{baseline.persisting_findings.length}</strong><small>需要继续跟进</small></div>
          </div>

          {GROUPS.map((group) => {
            const findings = groupFindings(baseline, group.key);
            if (findings.length === 0) return null;
            return (
              <details className={`baseline-group baseline-group-${group.key}`} key={group.key} open={group.key === 'new'}>
                <summary><span>{group.title}</span><span className="count-pill">{findings.length} 项</span></summary>
                <div className="baseline-rows">
                  {findings.map((finding) => (
                    <div className="baseline-row" key={`${group.key}-${finding.id}`}>
                      <SeverityBadge severity={finding.severity} />
                      <span className="baseline-row-title">{finding.title}</span>
                      <code>{finding.file_path}:{finding.line_start}</code>
                    </div>
                  ))}
                </div>
              </details>
            );
          })}

          {(baseline.new_vulnerabilities.length + baseline.resolved_vulnerabilities.length + baseline.persisting_vulnerabilities.length) > 0 && (
            <div className="baseline-vulns">
              <ShieldAlert size={16} />
              <span>依赖漏洞：新增 {baseline.new_vulnerabilities.length} · 已修复 {baseline.resolved_vulnerabilities.length} · 仍存在 {baseline.persisting_vulnerabilities.length}</span>
            </div>
          )}

          <p className="baseline-note">标题或文件路径变化较大时，同一问题可能被计为“新增 + 已修复”，请结合证据人工确认。</p>
        </>
      )}
    </section>
  );
}
