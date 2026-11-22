import type { Review } from '../types';

export function makeReview(overrides: Partial<Review> = {}): Review {
  return {
    id: 'review-1',
    source_type: 'github',
    source_name: 'example/shop-service',
    status: 'completed',
    stage: 'summary',
    stage_detail: '审查结果已汇总',
    progress: 100,
    error: null,
    created_at: '2026-11-21T08:00:00Z',
    updated_at: '2026-11-21T08:02:00Z',
    completed_at: '2026-11-21T08:02:00Z',
    coverage: {
      total_files: 10,
      eligible_files: 8,
      analyzed_files: 8,
      static_scanned_files: 8,
      languages: { TypeScript: 5, Python: 3 },
      skipped_files: [{ path: 'dist/bundle.js', reason: '构建产物，已跳过' }],
    },
    findings: [
      {
        id: 'finding-static-1', source: 'static', category: 'security', severity: 'high',
        title: '检测到疑似硬编码密钥', description: '源码中出现了直接赋值的高熵密钥，可能随仓库泄露。',
        suggestion: '移除密钥并轮换凭据，改用进程环境变量注入。',
        file_path: 'src/config.ts', line_start: 12, line_end: 12, evidence: 'const apiSecret = "REDACTED";',
      },
      {
        id: 'finding-ai-1', source: 'ai', category: 'design', severity: 'medium',
        title: '订单模块承担过多职责', description: '订单创建、库存更新和通知发送耦合在同一函数中，故障边界不清晰。',
        suggestion: '将库存与通知拆分为独立服务，并明确失败补偿策略。',
        file_path: 'src/orders.ts', line_start: 40, line_end: 52, evidence: 'export async function createOrder(input) {\n  // inventory, payment and notification\n}',
      },
      {
        id: 'finding-ai-2', source: 'ai', category: 'bug', severity: 'low',
        title: '空数组可能导致无效请求', description: '未提前处理空输入时，后续请求可能收到无效参数。',
        suggestion: '在构造请求前校验输入并直接返回空结果。',
        file_path: 'src/search.ts', line_start: 8, line_end: 8, evidence: 'const result = await query(ids);',
      },
    ],
    vulnerabilities: [
      {
        package_name: 'lodash', package_version: '4.17.20', ecosystem: 'npm',
        vulnerability_id: 'GHSA-example-0001', summary: '示例依赖存在需要人工确认的已知漏洞。',
        severity: 'high', references: ['https://osv.dev/vulnerability/GHSA-example-0001'],
      },
    ],
    warnings: [],
    dependency_status: 'completed',
    ...overrides,
  };
}

export const runningReview = makeReview({
  status: 'running', stage: 'ai_review', stage_detail: '正在审查 src/orders.ts', progress: 55,
  completed_at: null, coverage: undefined, findings: [], vulnerabilities: [], dependency_status: 'pending',
});
