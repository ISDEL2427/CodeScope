import type { AISettings, BaselineResponse, ModelDetection, Review } from './types';

export class ApiError extends Error {
  status: number;

  constructor(message: string, status = 0) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

async function parseError(response: Response): Promise<ApiError> {
  try {
    const body = (await response.json()) as { detail?: string };
    return new ApiError(body.detail || `请求失败（HTTP ${response.status}）`, response.status);
  } catch {
    return new ApiError(`请求失败（HTTP ${response.status}）`, response.status);
  }
}

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url, options);
  } catch {
    throw new ApiError('无法连接本地审查服务，请确认后端已启动。');
  }
  if (!response.ok) throw await parseError(response);
  return (await response.json()) as T;
}

export function createReview(data: FormData): Promise<Review> {
  return request<Review>('/api/reviews', { method: 'POST', body: data });
}

export function listReviews(): Promise<Review[]> {
  return request<Review[]>('/api/reviews');
}

export function getReview(id: string): Promise<Review> {
  return request<Review>(`/api/reviews/${encodeURIComponent(id)}`);
}

export function cancelReview(id: string): Promise<Review> {
  return request<Review>(`/api/reviews/${encodeURIComponent(id)}/cancel`, { method: 'POST' });
}

export function continueReview(id: string, data: FormData): Promise<Review> {
  return request<Review>(`/api/reviews/${encodeURIComponent(id)}/continue`, { method: 'POST', body: data });
}

export function finishReview(id: string): Promise<Review> {
  return request<Review>(`/api/reviews/${encodeURIComponent(id)}/finish`, { method: 'POST' });
}

export function getBaseline(id: string): Promise<BaselineResponse> {
  return request<BaselineResponse>(`/api/reviews/${encodeURIComponent(id)}/baseline`);
}

export function getAISettings(): Promise<AISettings> {
  return request<AISettings>('/api/ai/settings');
}

export function detectModels(baseUrl: string, apiKey?: string): Promise<ModelDetection> {
  return request<ModelDetection>('/api/ai/models/detect', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ base_url: baseUrl, api_key: apiKey || '' }),
  });
}

function reportFilename(contentDisposition: string | null, id: string, format: 'json' | 'markdown'): string {
  const encoded = contentDisposition?.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  const plain = contentDisposition?.match(/filename="?([^";]+)"?/i)?.[1];
  const extension = format === 'markdown' ? 'md' : 'json';
  if (encoded) {
    try {
      return decodeURIComponent(encoded);
    } catch {
      return `code-review-${id}.${extension}`;
    }
  }
  return plain || `code-review-${id}.${extension}`;
}

interface DesktopReportBridge {
  save_report?: (id: string, format: string) => Promise<{
    status?: 'saved' | 'cancelled' | 'error';
    message?: string;
    path?: string;
  }>;
}

function desktopReportBridge(): DesktopReportBridge | null {
  const api = (window as unknown as { pywebview?: { api?: DesktopReportBridge } }).pywebview?.api;
  return api && typeof api.save_report === 'function' ? api : null;
}

export async function downloadReport(id: string, format: 'json' | 'markdown'): Promise<void> {
  const bridge = desktopReportBridge();
  if (bridge?.save_report) {
    const result = await bridge.save_report(id, format);
    if (result?.status === 'error') {
      throw new ApiError(result.message || '保存报告失败。');
    }
    return;
  }

  let response: Response;
  try {
    response = await fetch(`/api/reviews/${encodeURIComponent(id)}/report?format=${format}`);
  } catch {
    throw new ApiError('无法连接本地审查服务，请确认后端已启动。');
  }
  if (!response.ok) throw await parseError(response);

  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = reportFilename(response.headers.get('content-disposition'), id, format);
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : '发生未知错误，请稍后重试。';
}
