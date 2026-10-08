/** The RAGForge API's JSON, as the dashboard uses it. */

export type DocumentStatus = "uploaded" | "processing" | "ready" | "failed" | "deleting";

export type DocumentInfo = {
  id: string;
  filename: string;
  mime_type: string;
  size_bytes: number;
  status: DocumentStatus;
  error: string | null;
  chunk_count: number;
  page_count: number | null;
  created_at: string;
  updated_at: string;
};

export type DocumentList = { items: DocumentInfo[]; next_cursor: string | null };

export type UploadResult = { document: DocumentInfo; duplicate: boolean };

export type ApiKeyKind = "secret" | "public";

export type ApiKeyInfo = {
  id: string;
  name: string;
  kind: ApiKeyKind;
  prefix: string;
  allowed_origins: string[];
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
};

export type CreatedApiKey = ApiKeyInfo & { key: string };

export type Citation = {
  number: number;
  document_id: string;
  filename: string;
  page: number | null;
  snippet: string;
  chunk_id: string;
};

export type ChatResult = {
  message_id: string;
  session_id: string;
  answer: string;
  citations: Citation[];
  usage: { prompt_tokens: number; completion_tokens: number };
  cache_hit: boolean;
  latency_ms: number;
};

export type DayUsage = {
  day: string;
  questions: number;
  cache_hits: number;
  tokens_in: number;
  tokens_out: number;
  cost_usd: number;
  latency_p50_ms: number | null;
  latency_p95_ms: number | null;
};

export type UsageReport = {
  start: string;
  end: string;
  days: DayUsage[];
  totals: {
    questions: number;
    cache_hits: number;
    cache_hit_rate: number;
    tokens_in: number;
    tokens_out: number;
    cost_usd: number;
    latency_p50_ms: number | null;
    latency_p95_ms: number | null;
  };
};

export type Me = {
  tenant_id: string;
  tenant_name: string;
  plan: "free" | "pro";
  auth_type: "user" | "api_key";
  email: string | null;
  role: "owner" | "admin" | "member" | null;
};
