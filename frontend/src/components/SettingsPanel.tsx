"use client";

import { useEffect, useState, type FormEvent } from "react";

import {
  addMcpServer,
  deleteMcpServer,
  discoverMcp,
  googleAuthorize,
  googleStatus,
  listMcpServers,
  revokeGoogle,
  saveGoogleClient,
  setWhitelist,
  type GoogleStatus,
  type McpServer,
} from "@/lib/slice4";

export function SettingsPanel() {
  const [google, setGoogle] = useState<GoogleStatus | null>(null);
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [servers, setServers] = useState<McpServer[]>([]);
  const [externalEnabled, setExternalEnabled] = useState(false);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [token, setToken] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  async function refresh() {
    const [status, mcp] = await Promise.all([googleStatus(), listMcpServers()]);
    setGoogle(status);
    setServers(mcp.servers);
    setExternalEnabled(mcp.external_tools_enabled);
  }

  useEffect(() => {
    refresh().catch((caught: unknown) => {
      setError(caught instanceof Error ? caught.message : "Could not load settings");
    });
  }, []);

  async function onSaveClient(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      setGoogle(await saveGoogleClient(clientId, clientSecret));
      setClientSecret("");
      setNote("OAuth client saved in the encrypted credential store.");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not save the client");
    }
  }

  async function onConnect() {
    setError(null);
    try {
      window.location.href = await googleAuthorize();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not start Google consent");
    }
  }

  async function onRevoke() {
    setError(null);
    try {
      const detail = await revokeGoogle();
      setNote(detail);
      await refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not revoke Google");
    }
  }

  async function onAddServer(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      await addMcpServer(name, url, token);
      setName("");
      setUrl("");
      setToken("");
      await refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not add the server");
    }
  }

  return (
    <div className="max-w-2xl">
      <h1 className="font-serif text-3xl">Settings</h1>
      <p className="mt-3 text-sm leading-6 text-muted">
        Google stays read-only. External MCP tools stay off until you whitelist each one. Model
        names stay in the environment.
      </p>
      {error ? <p className="mt-3 text-sm text-bad">{error}</p> : null}
      {note ? <p className="mt-3 text-sm text-ok">{note}</p> : null}

      <section className="mt-8 border border-line bg-white/40 p-4">
        <h2 className="font-serif text-2xl">Google</h2>
        <p className="mt-2 text-sm text-muted">
          Workspace-Internal OAuth client. Scopes are gmail.readonly and calendar.events.readonly.
          Redirect URI: {google?.redirect_uri ?? "loading…"}
        </p>
        {google ? (
          <p className="mt-2 text-sm">
            {google.connected
              ? `Connected. Refresh token age ${google.refresh_age_days ?? "unknown"} days.`
              : google.configured
                ? "Client saved. Not connected."
                : "No client saved."}
            {google.weekly_reconsent
              ? " A consumer app in Testing status needs a new consent after 7 days."
              : ""}
          </p>
        ) : null}
        <form onSubmit={onSaveClient} className="mt-3 grid gap-2">
          <input
            value={clientId}
            onChange={(event) => setClientId(event.target.value)}
            placeholder="Client id"
            className="border border-line bg-white/70 px-3 py-2 text-sm"
            required
          />
          <input
            value={clientSecret}
            onChange={(event) => setClientSecret(event.target.value)}
            placeholder="Client secret"
            type="password"
            className="border border-line bg-white/70 px-3 py-2 text-sm"
            required
          />
          <div className="flex gap-2">
            <button type="submit" className="bg-ink px-4 py-2 text-sm text-paper">
              Save client
            </button>
            <button type="button" onClick={() => void onConnect()} className="px-4 py-2 text-sm">
              Connect Google
            </button>
            <button type="button" onClick={() => void onRevoke()} className="px-4 py-2 text-sm text-bad">
              Revoke and wipe
            </button>
          </div>
        </form>
      </section>

      <section className="mt-6 border border-line bg-white/40 p-4">
        <h2 className="font-serif text-2xl">External MCP</h2>
        <p className="mt-2 text-sm text-muted">
          Discovered tools are disabled. Whitelist one before the project chat may call it.
          {externalEnabled ? " At least one tool is whitelisted." : " None are whitelisted."}
        </p>
        <form onSubmit={onAddServer} className="mt-3 grid gap-2">
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Name"
            className="border border-line bg-white/70 px-3 py-2 text-sm"
            required
          />
          <input
            value={url}
            onChange={(event) => setUrl(event.target.value)}
            placeholder="https://host/mcp"
            className="border border-line bg-white/70 px-3 py-2 text-sm"
            required
          />
          <input
            value={token}
            onChange={(event) => setToken(event.target.value)}
            placeholder="Bearer token, optional"
            type="password"
            className="border border-line bg-white/70 px-3 py-2 text-sm"
          />
          <button type="submit" className="justify-self-start bg-ink px-4 py-2 text-sm text-paper">
            Add server
          </button>
        </form>
        <ul className="mt-4 space-y-4">
          {servers.map((server) => (
            <li key={server.id} className="border border-line p-3">
              <p className="font-serif">{server.name}</p>
              <p className="break-all text-xs text-muted">{server.url}</p>
              <div className="mt-2 flex gap-3 text-sm">
                <button type="button" onClick={() => void discoverMcp(server.id).then(refresh)}>
                  Discover
                </button>
                <button
                  type="button"
                  className="text-bad"
                  onClick={() => void deleteMcpServer(server.id).then(refresh)}
                >
                  Remove
                </button>
              </div>
              <ul className="mt-2 space-y-1">
                {server.tools.map((tool) => (
                  <li key={tool.tool_name} className="text-sm">
                    <label className="flex items-start gap-2">
                      <input
                        type="checkbox"
                        checked={tool.whitelisted}
                        onChange={(event) =>
                          void setWhitelist(server.id, tool.tool_name, event.target.checked).then(
                            refresh,
                          )
                        }
                      />
                      <span>
                        {tool.tool_name}
                        <span className="block text-xs text-muted">
                          Side effects possible. {tool.description}
                        </span>
                      </span>
                    </label>
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
