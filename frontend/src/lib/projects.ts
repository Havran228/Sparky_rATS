export type SalaryExtract = {
  minimum: string | null;
  maximum: string | null;
  currency: string;
  period: string;
  basis: string;
  raw: string;
};

export type Requirements = {
  mandatory?: string[];
  preferred?: string[];
};

export type Project = {
  id: string;
  source_kind: string;
  source_ref: string | null;
  company: string | null;
  title: string | null;
  contract_type: string | null;
  salary: SalaryExtract | null;
  language: string | null;
  requirements: Requirements | null;
  offerings: string[] | null;
  contacts: string[] | null;
  raw_content_markdown: string | null;
  current_status: string;
  prompt_version: string | null;
  created_at: string;
};

export type JobView = {
  id: string;
  status: string;
  progress: number;
  progress_message: string;
  error: string | null;
  result: Record<string, unknown> | null;
};

export type LevyLine = { code: string; amount: string };
export type BracketLine = { rate: string; portion: string; amount: string };

export type SalaryBreakdown = {
  modeled: boolean;
  contract_type: string;
  tax_year: number;
  message: string | null;
  gross: string | null;
  employee_social: LevyLine[];
  employee_social_total: string | null;
  employee_health: string | null;
  employee_total: string | null;
  tax_base: string | null;
  nczd_applied: string | null;
  taxable: string | null;
  tax_brackets: BracketLine[];
  tax: string | null;
  net: string | null;
  employer_social_total: string | null;
  employer_health: string | null;
  employer_total: string | null;
  total_cost: string | null;
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

export async function listProjects(): Promise<Project[]> {
  const response = await api("/projects");
  if (!response.ok) {
    throw new Error(await errorDetail(response));
  }
  const body = (await response.json()) as { projects: Project[] };
  return body.projects;
}

export async function deleteProject(id: string): Promise<void> {
  const response = await api(`/projects/${id}`, { method: "DELETE" });
  if (!response.ok) {
    throw new Error(await errorDetail(response));
  }
}

export async function getProject(id: string): Promise<Project> {
  const response = await api(`/projects/${id}`);
  if (!response.ok) {
    throw new Error(await errorDetail(response));
  }
  return (await response.json()) as Project;
}

export async function startIngest(input: string): Promise<string> {
  const response = await api("/projects/ingest", {
    method: "POST",
    body: JSON.stringify({ input }),
  });
  if (!response.ok) {
    throw new Error(await errorDetail(response));
  }
  const body = (await response.json()) as { job_id: string };
  return body.job_id;
}

export async function getJob(id: string): Promise<JobView> {
  const response = await api(`/jobs/${id}`);
  if (!response.ok) {
    throw new Error(await errorDetail(response));
  }
  return (await response.json()) as JobView;
}

export async function salaryYears(): Promise<number[]> {
  const response = await api("/salary/years");
  if (!response.ok) {
    throw new Error(await errorDetail(response));
  }
  const body = (await response.json()) as { years: number[] };
  return body.years;
}

export async function calculateNet(input: {
  gross: string;
  taxYear: number;
  claimNczd: boolean;
  contractType: string;
}): Promise<SalaryBreakdown> {
  const response = await api("/salary/net", {
    method: "POST",
    body: JSON.stringify({
      gross: input.gross,
      tax_year: input.taxYear,
      claim_nczd: input.claimNczd,
      contract_type: input.contractType,
    }),
  });
  if (!response.ok) {
    throw new Error(await errorDetail(response));
  }
  return (await response.json()) as SalaryBreakdown;
}

export function formatEur(value: string | null | undefined): string {
  if (!value) {
    return "—";
  }
  const number = Number(value);
  if (Number.isNaN(number)) {
    return value;
  }
  return new Intl.NumberFormat("sk-SK", { style: "currency", currency: "EUR" }).format(number);
}

async function errorDetail(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: string };
    return body.detail ?? `Request failed (${response.status})`;
  } catch {
    return `Request failed (${response.status})`;
  }
}
