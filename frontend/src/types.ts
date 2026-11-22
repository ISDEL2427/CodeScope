export type SourceType = 'github' | 'zip';
export type ReviewStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled' | 'interrupted';
export type FindingSource = 'static' | 'ai';
export type FindingCategory = 'bug' | 'design' | 'security' | 'maintainability';
export type Severity = 'critical' | 'high' | 'medium' | 'low' | 'info';

export interface SkippedFile {
  path: string;
  reason: string;
}

export interface Coverage {
  total_files: number;
  eligible_files?: number;
  analyzed_files: number;
  static_scanned_files?: number;
  languages: Record<string, number>;
  skipped_files: SkippedFile[];
}

export interface Finding {
  id: string;
  source: FindingSource;
  category: FindingCategory;
  severity: Severity;
  title: string;
  description: string;
  suggestion: string;
  file_path: string;
  line_start: number;
  line_end: number;
  evidence: string;
}

export interface Vulnerability {
  package_name: string;
  package_version: string;
  ecosystem: string;
  vulnerability_id: string;
  summary: string;
  severity: string;
  references: string[];
}

export interface Review {
  id: string;
  source_type: SourceType;
  source_name: string;
  status: ReviewStatus;
  stage: string | null;
  stage_detail: string | null;
  progress: number;
  error: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  coverage?: Coverage;
  findings?: Finding[];
  vulnerabilities?: Vulnerability[];
  warnings?: string[];
  dependency_status?: string;
  awaiting_decision?: boolean;
  depth?: number;
  ai_budget?: number;
  ai_base_url?: string;
  ai_model?: string;
}

export interface AISettings {
  base_url: string;
  model: string;
  has_key: boolean;
}

export interface ModelDetection {
  models: string[];
  suggested: string | null;
  source: 'provider' | 'heuristic' | 'none' | string;
  suggested_base_url?: string | null;
}

export interface BaselineComparison {
  current_id: string;
  current_created_at: string;
  baseline_id: string;
  baseline_created_at: string;
  new_findings: Finding[];
  resolved_findings: Finding[];
  persisting_findings: Finding[];
  new_vulnerabilities: Vulnerability[];
  resolved_vulnerabilities: Vulnerability[];
  persisting_vulnerabilities: Vulnerability[];
}

export interface BaselineResponse {
  baseline: BaselineComparison | null;
  message?: string | null;
}
