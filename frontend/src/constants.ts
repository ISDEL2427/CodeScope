import type { FindingCategory, FindingSource, ReviewStatus, Severity } from './types';

export const statusLabels: Record<ReviewStatus, string> = {
  queued: '排队中',
  running: '审查中',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
  interrupted: '已中断',
};

export const severityLabels: Record<Severity, string> = {
  critical: '严重',
  high: '高',
  medium: '中',
  low: '低',
  info: '提示',
};

export const categoryLabels: Record<FindingCategory, string> = {
  bug: '代码缺陷',
  design: '设计问题',
  security: '安全风险',
  maintainability: '可维护性',
};

export const sourceLabels: Record<FindingSource, string> = {
  static: '静态扫描',
  ai: 'AI 审查',
};

export const stageLabels: Record<string, string> = {
  ingest: '源码接入',
  ingestion: '源码接入',
  inventory: '文件清单',
  static_scan: '静态扫描',
  static: '静态扫描',
  ai_review: 'AI 审查',
  ai: 'AI 审查',
  dependency_scan: '依赖扫描',
  dependencies: '依赖扫描',
  osv: '依赖扫描',
  summary: '结果汇总',
  summarize: '结果汇总',
  completed: '结果汇总',
};

export const stages = ['ingest', 'inventory', 'static_scan', 'ai_review', 'dependency_scan', 'summary'] as const;

export function formatDate(value: string | null): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(date);
}

export function isActive(status: ReviewStatus): boolean {
  return status === 'queued' || status === 'running';
}
