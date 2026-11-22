import { afterEach, describe, expect, it, vi } from 'vitest';

import { downloadReport, getBaseline } from './api';

interface BridgeWindow extends Window {
  pywebview?: {
    api?: {
      save_report?: (id: string, format: string) => Promise<{ status?: string; message?: string; path?: string }>;
    };
  };
}

afterEach(() => {
  delete (window as BridgeWindow).pywebview;
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('downloadReport', () => {
  it('桌面环境使用原生保存对话框', async () => {
    const save = vi.fn().mockResolvedValue({ status: 'saved', path: 'C:/reports/report.json' });
    (window as BridgeWindow).pywebview = { api: { save_report: save } };

    await downloadReport('review-1', 'json');

    expect(save).toHaveBeenCalledWith('review-1', 'json');
  });

  it('取消保存时不报错', async () => {
    const save = vi.fn().mockResolvedValue({ status: 'cancelled' });
    (window as BridgeWindow).pywebview = { api: { save_report: save } };

    await expect(downloadReport('review-1', 'markdown')).resolves.toBeUndefined();
  });

  it('原生保存失败时抛出可展示的错误', async () => {
    const save = vi.fn().mockResolvedValue({ status: 'error', message: '写入报告失败：磁盘已满' });
    (window as BridgeWindow).pywebview = { api: { save_report: save } };

    await expect(downloadReport('review-1', 'json')).rejects.toThrow('磁盘已满');
  });
});

describe('getBaseline', () => {
  it('请求基线对比接口并正确编码任务标识', async () => {
    const payload = { baseline: null, message: '首次体检' };
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => payload });
    vi.stubGlobal('fetch', fetchMock);

    await expect(getBaseline('review-1')).resolves.toEqual(payload);
    expect(fetchMock).toHaveBeenCalledWith('/api/reviews/review-1/baseline', undefined);
  });
});
