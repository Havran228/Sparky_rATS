"use client";

import { useEffect, useState } from "react";

import { apiBase, type ComponentHealth, type Health, getHealth } from "@/lib/api";

const LABELS: { key: keyof Omit<Health, "status">; label: string }[] = [
  { key: "db", label: "SQLite" },
  { key: "qdrant", label: "Qdrant" },
  { key: "ollama", label: "Ollama" },
  { key: "watcher", label: "Inbox watcher" },
  { key: "mcp", label: "MCP port" },
];

export function HealthPanel() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    getHealth(controller.signal)
      .then((body) => {
        if (!controller.signal.aborted) {
          setHealth(body);
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setError(`The API at ${apiBase()} did not respond.`);
        }
      });
    return () => controller.abort();
  }, []);

  if (error) {
    return (
      <section className="border border-line bg-white/60 p-4">
        <h2 className="font-serif text-xl">API unreachable</h2>
        <p className="mt-2 text-sm text-muted">{error} Start the backend and refresh.</p>
      </section>
    );
  }

  if (!health) {
    return <p className="text-sm text-muted">Checking the local API…</p>;
  }

  return (
    <section>
      <div className="flex items-baseline justify-between gap-4">
        <h2 className="font-serif text-xl">Local services</h2>
        <StatusPill ok={health.status === "ok"} label={health.status} />
      </div>
      <ul className="mt-4 divide-y divide-line border border-line bg-white/60">
        {LABELS.map((item) => (
          <ComponentRow key={item.key} label={item.label} component={health[item.key]} />
        ))}
      </ul>
    </section>
  );
}

function ComponentRow({ label, component }: { label: string; component: ComponentHealth }) {
  const models = component.loaded_models?.length ? component.loaded_models.join(", ") : null;
  const off = component.detail === "disabled";
  const detail = off ? null : component.detail || models;
  return (
    <li className="flex items-center justify-between gap-4 px-4 py-3 text-sm">
      <span>{label}</span>
      <span className="text-right text-muted">
        <StatusPill
          ok={component.ok}
          neutral={off}
          label={component.ok ? "up" : off ? "off" : "down"}
        />
        {detail ? <span className="ml-3">{detail}</span> : null}
      </span>
    </li>
  );
}

function StatusPill({ ok, label, neutral = false }: { ok: boolean; label: string; neutral?: boolean }) {
  const tone = neutral
    ? "bg-line text-muted"
    : ok
      ? "bg-[#e7f0d4] text-ok"
      : "bg-[#fde2e4] text-bad";
  return (
    <span className={`inline-block rounded-full px-2 py-0.5 text-xs uppercase tracking-wide ${tone}`}>
      {label}
    </span>
  );
}
