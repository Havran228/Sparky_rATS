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

async function errorDetail(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: string };
    return body.detail ?? `Request failed (${response.status})`;
  } catch {
    return `Request failed (${response.status})`;
  }
}

export type BoardCard = {
  id: string;
  title: string | null;
  company: string | null;
  current_status: string;
  contract_type: string | null;
};

export type BoardColumn = {
  status: string;
  label: string;
  projects: BoardCard[];
};

export type Stage = {
  id: string;
  name: string;
  status: string;
  progress: string;
  position: number;
  notes: string | null;
};

export async function getBoard(): Promise<BoardColumn[]> {
  const response = await api("/board");
  if (!response.ok) throw new Error(await errorDetail(response));
  const body = (await response.json()) as { columns: BoardColumn[] };
  return body.columns;
}

export async function moveProject(id: string, column: string): Promise<void> {
  const response = await api(`/projects/${id}/move`, {
    method: "POST",
    body: JSON.stringify({ column }),
  });
  if (!response.ok) throw new Error(await errorDetail(response));
}

export async function listStages(projectId: string): Promise<{ current_status: string; stages: Stage[] }> {
  const response = await api(`/projects/${projectId}/stages`);
  if (!response.ok) throw new Error(await errorDetail(response));
  return (await response.json()) as { current_status: string; stages: Stage[] };
}

export type TimelineItem = {
  kind: "email" | "event";
  external_id: string;
  occurred_at: string;
  title: string;
  snippet: string;
  participants: string[];
};

export type Timeline = {
  domains: string[];
  emails: string[];
  lookback_days: number;
  items: TimelineItem[];
  warning: string | null;
};

export async function loadTimeline(
  projectId: string,
  domains: string[],
  lookbackDays: number,
): Promise<Timeline> {
  const response = await api(`/projects/${projectId}/timeline`, {
    method: "POST",
    body: JSON.stringify({ domains, lookback_days: lookbackDays }),
  });
  if (!response.ok) throw new Error(await errorDetail(response));
  return (await response.json()) as Timeline;
}

export async function attachMessage(projectId: string, messageId: string): Promise<void> {
  const response = await api(`/projects/${projectId}/timeline/attach`, {
    method: "POST",
    body: JSON.stringify({ message_id: messageId }),
  });
  if (!response.ok) throw new Error(await errorDetail(response));
}

export type Draft = {
  id: string;
  subject: string;
  body: string;
  created_at: string;
};

export async function listDrafts(projectId: string): Promise<Draft[]> {
  const response = await api(`/projects/${projectId}/drafts`);
  if (!response.ok) throw new Error(await errorDetail(response));
  const body = (await response.json()) as { drafts: Draft[] };
  return body.drafts;
}

export async function createDraft(projectId: string, subject: string, body: string): Promise<void> {
  const response = await api(`/projects/${projectId}/drafts`, {
    method: "POST",
    body: JSON.stringify({ subject, body }),
  });
  if (!response.ok) throw new Error(await errorDetail(response));
}

export async function deleteDraft(id: string): Promise<void> {
  const response = await api(`/drafts/${id}`, { method: "DELETE" });
  if (!response.ok) throw new Error(await errorDetail(response));
}

export type ChatMessage = {
  id: string;
  role: string;
  content: string;
  tool_name: string | null;
  referenced_chunks: string[];
};

export async function startChat(projectId: string, message: string, threadId: string | null): Promise<{
  job_id: string;
  thread_id: string;
}> {
  const response = await api(`/projects/${projectId}/chat`, {
    method: "POST",
    body: JSON.stringify({ message, thread_id: threadId }),
  });
  if (!response.ok) throw new Error(await errorDetail(response));
  return (await response.json()) as { job_id: string; thread_id: string };
}

export async function listMessages(projectId: string, threadId: string): Promise<ChatMessage[]> {
  const response = await api(`/projects/${projectId}/chats?thread_id=${encodeURIComponent(threadId)}`);
  if (!response.ok) throw new Error(await errorDetail(response));
  const body = (await response.json()) as { messages: ChatMessage[] };
  return body.messages;
}

export type GoogleStatus = {
  configured: boolean;
  connected: boolean;
  client_id: string | null;
  scopes: string[];
  refresh_age_days: number | null;
  redirect_uri: string;
  weekly_reconsent: boolean;
};

export async function googleStatus(): Promise<GoogleStatus> {
  const response = await api("/google/status");
  if (!response.ok) throw new Error(await errorDetail(response));
  return (await response.json()) as GoogleStatus;
}

export async function saveGoogleClient(clientId: string, clientSecret: string): Promise<GoogleStatus> {
  const response = await api("/google/client", {
    method: "PUT",
    body: JSON.stringify({ client_id: clientId, client_secret: clientSecret }),
  });
  if (!response.ok) throw new Error(await errorDetail(response));
  return (await response.json()) as GoogleStatus;
}

export async function googleAuthorize(): Promise<string> {
  const response = await api("/google/authorize");
  if (!response.ok) throw new Error(await errorDetail(response));
  const body = (await response.json()) as { url: string };
  return body.url;
}

export async function exchangeGoogleCode(code: string, state: string): Promise<void> {
  const response = await api("/google/exchange", {
    method: "POST",
    body: JSON.stringify({ code, state }),
  });
  if (!response.ok) throw new Error(await errorDetail(response));
}

export async function revokeGoogle(): Promise<string> {
  const response = await api("/google/revoke", { method: "POST" });
  if (!response.ok) throw new Error(await errorDetail(response));
  const body = (await response.json()) as { detail: string };
  return body.detail;
}

export type McpTool = {
  tool_name: string;
  description: string;
  whitelisted: boolean;
  side_effects: boolean;
};

export type McpServer = {
  id: string;
  name: string;
  url: string;
  has_token: boolean;
  tools: McpTool[];
};

export async function listMcpServers(): Promise<{ servers: McpServer[]; external_tools_enabled: boolean }> {
  const response = await api("/settings/mcp-servers");
  if (!response.ok) throw new Error(await errorDetail(response));
  return (await response.json()) as { servers: McpServer[]; external_tools_enabled: boolean };
}

export async function addMcpServer(name: string, url: string, token: string): Promise<void> {
  const response = await api("/settings/mcp-servers", {
    method: "POST",
    body: JSON.stringify({ name, url, token: token || null }),
  });
  if (!response.ok) throw new Error(await errorDetail(response));
}

export async function discoverMcp(id: string): Promise<void> {
  const response = await api(`/settings/mcp-servers/${id}/discover`, { method: "POST" });
  if (!response.ok) throw new Error(await errorDetail(response));
}

export async function setWhitelist(serverId: string, toolName: string, whitelisted: boolean): Promise<void> {
  const response = await api(
    `/settings/mcp-servers/${serverId}/tools/${encodeURIComponent(toolName)}`,
    { method: "PATCH", body: JSON.stringify({ whitelisted }) },
  );
  if (!response.ok) throw new Error(await errorDetail(response));
}

export async function deleteMcpServer(id: string): Promise<void> {
  const response = await api(`/settings/mcp-servers/${id}`, { method: "DELETE" });
  if (!response.ok) throw new Error(await errorDetail(response));
}
