import { expect, test, type Page } from '@playwright/test';
import { makeReview } from '../src/test/fixtures';

async function mockDetail(page: Page, id: string, review = makeReview({ id })) {
  await page.route(`**/api/reviews/${id}`, (route) => route.fulfill({ json: review }));
}

test('创建 GitHub 审查并轮询到完整结果', async ({ page }) => {
  const id = 'e2e-poll';
  let detailCalls = 0;
  await page.route('**/api/reviews', async (route) => {
    expect(route.request().method()).toBe('POST');
    const body = route.request().postData() || '';
    expect(body).toContain('https://github.com/example/shop-service');
    expect(body).toContain('test-e2e-secret');
    expect(body).toContain('gpt-4o-mini');
    await route.fulfill({
      json: makeReview({ id, status: 'queued', stage: null, stage_detail: '任务已创建', progress: 0, completed_at: null, coverage: undefined, findings: [], vulnerabilities: [] }),
    });
  });
  await page.route(`**/api/reviews/${id}`, (route) => {
    detailCalls += 1;
    const running = makeReview({ id, status: 'running', stage: 'ai_review', stage_detail: '正在执行分块 AI 审查', progress: 55, completed_at: null, coverage: undefined, findings: [], vulnerabilities: [] });
    return route.fulfill({ json: detailCalls <= 2 ? running : makeReview({ id }) });
  });

  await page.goto('/');
  await page.getByLabel('仓库地址').fill('https://github.com/example/shop-service');
  await page.getByLabel('API Key').fill('test-e2e-secret');
  await page.getByRole('button', { name: '开始审查' }).click();
  await expect(page).toHaveURL(new RegExp(`/reviews/${id}$`));
  await expect(page.getByRole('heading', { name: '审查正在进行' })).toBeVisible();
  await expect(page.getByText('检测到疑似硬编码密钥')).toBeVisible({ timeout: 6000 });
  await expect(page.getByRole('heading', { name: '已完成' })).toBeVisible();
  expect(detailCalls).toBeGreaterThanOrEqual(3);
});

test('筛选代码发现并展开对应证据', async ({ page }) => {
  await mockDetail(page, 'e2e-filters');
  await page.goto('/reviews/e2e-filters');
  await expect(page.getByText('检测到疑似硬编码密钥')).toBeVisible();

  await page.getByLabel('严重度').selectOption('high');
  await expect(page.getByText('检测到疑似硬编码密钥')).toBeVisible();
  await expect(page.getByText('订单模块承担过多职责')).toHaveCount(0);
  await page.getByText('查看代码证据').click();
  await expect(page.getByLabel('src/config.ts 代码证据')).toBeVisible();
  await expect(page.getByLabel('src/config.ts 代码证据')).toContainText('const apiSecret');
  await page.getByRole('button', { name: '清除筛选' }).click();

  await page.getByLabel('类别').selectOption('design');
  await expect(page.getByText('订单模块承担过多职责')).toBeVisible();
  await expect(page.getByText('检测到疑似硬编码密钥')).toHaveCount(0);
  await page.getByRole('button', { name: '清除筛选' }).click();

  await page.getByLabel('来源').selectOption('static');
  await expect(page.getByText('1 / 3 项')).toBeVisible();
  await expect(page.getByText('空数组可能导致无效请求')).toHaveCount(0);
});

test('创建失败时显示后端 detail 错误', async ({ page }) => {
  await page.route('**/api/reviews', (route) => route.fulfill({ status: 422, json: { detail: 'API Key 无效，请检查后重试。' } }));
  await page.goto('/');
  await page.getByLabel('仓库地址').fill('https://github.com/example/shop-service');
  await page.getByLabel('API Key').fill('invalid-key');
  await page.getByRole('button', { name: '开始审查' }).click();
  await expect(page.getByRole('alert')).toContainText('API Key 无效，请检查后重试。');
  await expect(page).toHaveURL(/\/$/);
});

test('失败任务展示明确错误而不是完整成功状态', async ({ page }) => {
  await mockDetail(page, 'e2e-failed', makeReview({
    id: 'e2e-failed', status: 'failed', stage: 'ai_review', progress: 62,
    completed_at: null, error: 'AI 服务请求超时，任务已停止。', coverage: undefined, findings: [], vulnerabilities: [],
  }));
  await page.goto('/reviews/e2e-failed');
  await expect(page.getByRole('heading', { name: '失败' })).toBeVisible();
  await expect(page.getByText('AI 服务请求超时，任务已停止。')).toBeVisible();
  await expect(page.getByText('审查已部分完成')).toHaveCount(0);
});

test('取消运行任务并更新为已取消', async ({ page }) => {
  const id = 'e2e-cancel';
  let cancelled = false;
  const running = makeReview({ id, status: 'running', stage: 'static_scan', stage_detail: '正在执行静态扫描', progress: 35, completed_at: null, coverage: undefined, findings: [], vulnerabilities: [] });
  await page.route(`**/api/reviews/${id}`, (route) => route.fulfill({
    json: cancelled ? makeReview({ ...running, status: 'cancelled', stage_detail: '用户取消了任务' }) : running,
  }));
  await page.route(`**/api/reviews/${id}/cancel`, (route) => {
    cancelled = true;
    return route.fulfill({ json: makeReview({ ...running, status: 'cancelled', stage_detail: '用户取消了任务' }) });
  });
  await page.goto(`/reviews/${id}`);
  await page.getByRole('button', { name: '取消审查' }).click();
  await expect(page.getByRole('heading', { name: '已取消' })).toBeVisible();
  await expect(page.getByRole('button', { name: '取消审查' })).toHaveCount(0);
});

test('下载 Markdown 和 JSON 报告并保留安全文件名', async ({ page }) => {
  await mockDetail(page, 'e2e-download');
  const requests: string[] = [];
  await page.route(/\/api\/reviews\/e2e-download\/report\?format=(json|markdown)$/, (route) => {
    const format = new URL(route.request().url()).searchParams.get('format')!;
    requests.push(format);
    const markdown = format === 'markdown';
    return route.fulfill({
      status: 200,
      headers: {
        'content-type': markdown ? 'text/markdown; charset=utf-8' : 'application/json',
        'content-disposition': `attachment; filename="review-report.${markdown ? 'md' : 'json'}"`,
      },
      body: markdown ? '# 代码审查报告\n' : JSON.stringify({ id: 'e2e-download' }),
    });
  });
  await page.goto('/reviews/e2e-download');

  const markdownEvent = page.waitForEvent('download');
  await page.getByRole('button', { name: '下载 Markdown' }).click();
  const markdownDownload = await markdownEvent;
  expect(markdownDownload.suggestedFilename()).toBe('review-report.md');

  const jsonEvent = page.waitForEvent('download');
  await page.getByRole('button', { name: '下载 JSON' }).click();
  const jsonDownload = await jsonEvent;
  expect(jsonDownload.suggestedFilename()).toBe('review-report.json');
  expect(requests).toEqual(['markdown', 'json']);
});

test('部分完成警告与依赖不可用状态可见', async ({ page }) => {
  await mockDetail(page, 'e2e-partial', makeReview({
    id: 'e2e-partial', warnings: ['OSV 服务暂不可用，依赖结果不完整。'],
    dependency_status: 'unavailable', vulnerabilities: [],
  }));
  await page.goto('/reviews/e2e-partial');
  await expect(page.getByText('审查已部分完成')).toBeVisible();
  await expect(page.getByText('OSV 服务暂不可用，依赖结果不完整。')).toBeVisible();
  await expect(page.getByText('依赖扫描结果不可用')).toBeVisible();
});
