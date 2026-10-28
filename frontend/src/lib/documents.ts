import { pollJob } from "@/lib/knowledge";

export type RoundtripTerm = {
  term: string;
  group: string;
  hit_count: number;
  filter_hazard: boolean;
};

export type Roundtrip = {
  extractor?: string;
  text_chars?: number;
  mandatory_missing?: string[];
  terms?: RoundtripTerm[];
  grounding?: {
    mode?: string;
    dropped_skills?: string[];
    dropped_experience?: string[];
    dropped_education?: string[];
  };
};

export type GeneratedDocument = {
  id: string;
  project_id: string;
  evaluation_id: string | null;
  doc_type: "CV" | "COVER_LETTER";
  language: "en" | "sk";
  template: string;
  version: number;
  content: Record<string, unknown>;
  pdf_path: string | null;
  ats_roundtrip_report: Roundtrip | null;
  model_name: string | null;
  prompt_version: string | null;
  is_submitted: boolean;
  created_at: string;
};

export type JsonChange = {
  path: string;
  kind: "added" | "removed" | "changed";
  left?: unknown;
  right?: unknown;
};

async function api(path: string, init?: RequestInit): Promise<Response> {
  return fetch(`/api/backend${path}`, {
    ...init,
    headers: {
      "content-type": "application/json",
      ...(init?.headers ?? {}),
    },
    cache: "no-store",
  });
}

export async function listDocuments(projectId: string): Promise<GeneratedDocument[]> {
  const response = await api(`/projects/${projectId}/documents`);
  if (!response.ok) {
    throw new Error(await errorDetail(response));
  }
  const body = (await response.json()) as { documents: GeneratedDocument[] };
  return body.documents;
}

export async function generateDocument(
  projectId: string,
  input: {
    doc_type: GeneratedDocument["doc_type"];
    language: GeneratedDocument["language"];
    bundle_ids: string[];
    evaluation_id?: string | null;
    raw_markup?: string;
  },
): Promise<string> {
  const response = await api(`/projects/${projectId}/documents`, {
    method: "POST",
    body: JSON.stringify(input),
  });
  if (!response.ok) {
    throw new Error(await errorDetail(response));
  }
  const payload = (await response.json()) as { job_id: string };
  return payload.job_id;
}

export async function diffDocuments(leftId: string, rightId: string): Promise<JsonChange[]> {
  const response = await api(`/documents/${leftId}/diff/${rightId}`);
  if (!response.ok) {
    throw new Error(await errorDetail(response));
  }
  const body = (await response.json()) as { changes: JsonChange[] };
  return body.changes;
}

export async function submitDocument(documentId: string): Promise<string> {
  const response = await api(`/documents/${documentId}/submit`, { method: "POST" });
  if (!response.ok) {
    throw new Error(await errorDetail(response));
  }
  const payload = (await response.json()) as { job_id: string };
  return payload.job_id;
}

export async function runDocumentJob(jobId: string, onStatus: (message: string) => void) {
  const job = await pollJob(jobId, onStatus);
  if (job.status === "FAILED") {
    throw new Error(job.error ?? "Document job failed");
  }
  if (job.status === "CANCELLED") {
    throw new Error("Document job was cancelled");
  }
  return job;
}

export function pdfUrl(documentId: string): string {
  return `/api/backend/documents/${documentId}/pdf`;
}

async function errorDetail(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: string };
    return body.detail ?? `Request failed (${response.status})`;
  } catch {
    return `Request failed (${response.status})`;
  }
}
