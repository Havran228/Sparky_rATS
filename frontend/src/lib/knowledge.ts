import { getJob, type JobView } from "@/lib/projects";

export type Bundle = {
  id: string;
  name: string;
  kind: "historical_baseline" | "active_application";
  project_id: string | null;
  document_count?: number;
  documents?: BundleDocument[];
  created_at: string;
};

export type BundleDocument = {
  id: string;
  filename: string;
  doc_type: string;
  language: string | null;
  chunk_count: number;
  content_hash: string | null;
};

export type Skill = {
  skill: string;
  years: number | null;
  last_used: string | null;
  evidence_bundle_ids: string[];
  confirmed: boolean;
};

export type Profile = {
  identity: { name: string | null };
  contact: { email: string | null; phone: string | null; location: string | null };
  links: string[];
  languages: { language: string; cefr: string | null }[];
  skills: Skill[];
  preferences: {
    claim_nczd: boolean;
    preferred_contract_types: string[];
    locations: string[];
  };
  updated_at: string | null;
};

export type Citation = {
  chunk_id: string;
  filename?: string;
  section_header?: string;
  excerpt?: string;
  missing: boolean;
};

export type ClassifiedItem = {
  term: string;
  group: string;
  category: string;
  light: "GREEN" | "YELLOW" | "RED";
  rationale: string;
  citation_downgraded: boolean;
  citations: Citation[];
};

export type Evaluation = {
  id: string;
  project_id: string;
  match_percentage: number;
  model_name: string;
  prompt_version: string;
  created_at: string;
  score_weights: Record<string, number>;
  lexical_report: {
    alias_version: string;
    terms: { term: string; filter_hazard: boolean; hit_count: number; group: string }[];
  };
  classification: {
    items: ClassifiedItem[];
    profile_skills_used: string[];
  };
};

const DOC_TYPES = ["CV", "COVER_LETTER", "JOB_POSTING", "CORRESPONDENCE", "SCREENSHOT", "OTHER"];

export { DOC_TYPES };

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

export async function listBundles(): Promise<Bundle[]> {
  const response = await api("/bundles");
  if (!response.ok) {
    throw new Error(await errorDetail(response));
  }
  const body = (await response.json()) as { bundles: Bundle[] };
  return body.bundles;
}

export async function createBundle(input: {
  name: string;
  kind: Bundle["kind"];
}): Promise<Bundle> {
  const response = await api("/bundles", { method: "POST", body: JSON.stringify(input) });
  if (!response.ok) {
    throw new Error(await errorDetail(response));
  }
  return (await response.json()) as Bundle;
}

export async function getBundle(id: string): Promise<Bundle> {
  const response = await api(`/bundles/${id}`);
  if (!response.ok) {
    throw new Error(await errorDetail(response));
  }
  return (await response.json()) as Bundle;
}

export async function deleteBundle(id: string): Promise<void> {
  const response = await api(`/bundles/${id}`, { method: "DELETE" });
  if (!response.ok) {
    throw new Error(await errorDetail(response));
  }
}

export async function uploadDocument(bundleId: string, file: File, docType: string): Promise<string> {
  const body = new FormData();
  body.set("file", file);
  body.set("doc_type", docType);
  const response = await fetch(`/api/backend/bundles/${bundleId}/documents`, {
    method: "POST",
    body,
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(await errorDetail(response));
  }
  const payload = (await response.json()) as { job_id: string };
  return payload.job_id;
}

export async function getProfile(): Promise<Profile> {
  const response = await api("/profile");
  if (!response.ok) {
    throw new Error(await errorDetail(response));
  }
  return (await response.json()) as Profile;
}

export async function saveProfile(profile: Profile): Promise<Profile> {
  const { updated_at: _updated, ...body } = profile;
  const response = await api("/profile", { method: "PUT", body: JSON.stringify(body) });
  if (!response.ok) {
    throw new Error(await errorDetail(response));
  }
  return (await response.json()) as Profile;
}

export async function proposeSkills(bundleIds: string[]): Promise<string> {
  const response = await api("/profile/propose", {
    method: "POST",
    body: JSON.stringify({ bundle_ids: bundleIds }),
  });
  if (!response.ok) {
    throw new Error(await errorDetail(response));
  }
  const payload = (await response.json()) as { job_id: string };
  return payload.job_id;
}

export async function startEvaluation(projectId: string, bundleIds: string[]): Promise<string> {
  const response = await api(`/projects/${projectId}/evaluate`, {
    method: "POST",
    body: JSON.stringify({ bundle_ids: bundleIds }),
  });
  if (!response.ok) {
    throw new Error(await errorDetail(response));
  }
  const payload = (await response.json()) as { job_id: string };
  return payload.job_id;
}

export async function listEvaluations(projectId: string): Promise<Evaluation[]> {
  const response = await api(`/projects/${projectId}/evaluations`);
  if (!response.ok) {
    throw new Error(await errorDetail(response));
  }
  const body = (await response.json()) as { evaluations: Evaluation[] };
  return body.evaluations;
}

export async function pollJob(id: string, onStatus: (message: string) => void): Promise<JobView> {
  const deadline = Date.now() + 180000;
  while (Date.now() < deadline) {
    const job = await getJob(id);
    onStatus(`${job.progress}% ${job.progress_message || job.status}`.trim());
    if (job.status === "DONE" || job.status === "FAILED" || job.status === "CANCELLED") {
      return job;
    }
    await new Promise((resolve) => setTimeout(resolve, 400));
  }
  throw new Error("The job is still running. Refresh in a moment.");
}

async function errorDetail(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: string };
    return body.detail ?? `Request failed (${response.status})`;
  } catch {
    return `Request failed (${response.status})`;
  }
}
