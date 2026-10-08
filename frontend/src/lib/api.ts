export type ComponentHealth = {
  ok: boolean;
  detail?: string | null;
  loaded_models?: string[];
};

export type Health = {
  status: "ok" | "degraded";
  db: ComponentHealth;
  qdrant: ComponentHealth;
  ollama: ComponentHealth;
  watcher: ComponentHealth;
  mcp: ComponentHealth;
};

export function apiBase(): string {
  return process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8002";
}

export async function getHealth(signal?: AbortSignal): Promise<Health> {
  const response = await fetch(`${apiBase()}/health`, { cache: "no-store", signal });
  if (!response.ok) {
    throw new Error(`Health check returned ${response.status}`);
  }
  return response.json() as Promise<Health>;
}
