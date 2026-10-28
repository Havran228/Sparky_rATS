"use client";

import { useEffect, useState, type FormEvent } from "react";

import { getJob } from "@/lib/projects";
import {
  attachMessage,
  createDraft,
  deleteDraft,
  listDrafts,
  listMessages,
  loadTimeline,
  startChat,
  type ChatMessage,
  type Draft,
  type TimelineItem,
} from "@/lib/slice4";

export function TimelinePanel({ projectId, title }: { projectId: string; title: string }) {
  const [domain, setDomain] = useState("");
  const [lookback, setLookback] = useState(90);
  const [items, setItems] = useState<TimelineItem[]>([]);
  const [warning, setWarning] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function onLoad(event: FormEvent) {
    event.preventDefault();
    setPending(true);
    setError(null);
    setNote(null);
    try {
      const timeline = await loadTimeline(
        projectId,
        domain.trim() ? [domain.trim()] : [],
        lookback,
      );
      setItems(timeline.items);
      setWarning(timeline.warning);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not read the timeline");
    } finally {
      setPending(false);
    }
  }

  async function onAttach(messageId: string) {
    setPending(true);
    setError(null);
    try {
      await attachMessage(projectId, messageId);
      setNote("Saved into the application bundle as an email thread.");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not attach the message");
    } finally {
      setPending(false);
    }
  }

  return (
    <section className="border border-line bg-white/40 p-4">
      <h2 className="font-serif text-2xl">Timeline</h2>
      <p className="mt-1 text-sm text-muted">
        Read-only Gmail and Calendar. Nothing is sent. Attach a message only when you want it in
        the bundle.
      </p>
      <form onSubmit={onLoad} className="mt-3 flex flex-col gap-2 sm:flex-row">
        <input
          value={domain}
          onChange={(event) => setDomain(event.target.value)}
          placeholder="company domain, e.g. acme.com"
          className="min-w-0 flex-1 border border-line bg-white/70 px-3 py-2 text-sm"
        />
        <input
          type="number"
          min={1}
          max={365}
          value={lookback}
          onChange={(event) => setLookback(Number(event.target.value))}
          className="w-24 border border-line bg-white/70 px-3 py-2 text-sm"
          aria-label="Lookback days"
        />
        <button type="submit" disabled={pending} className="bg-ink px-4 py-2 text-sm text-paper">
          {pending ? "Reading…" : "Read timeline"}
        </button>
      </form>
      {warning ? <p className="mt-2 text-sm text-warn">{warning}</p> : null}
      {error ? <p className="mt-2 text-sm text-bad">{error}</p> : null}
      {note ? <p className="mt-2 text-sm text-ok">{note}</p> : null}
      <ul className="mt-4 divide-y divide-line">
        {items.length === 0 ? (
          <li className="py-3 text-sm text-muted">No timeline items yet.</li>
        ) : (
          items.map((item) => (
            <li key={`${item.kind}-${item.external_id}`} className="py-3">
              <p className="text-xs uppercase tracking-[0.14em] text-muted">
                {item.kind} · {item.occurred_at}
              </p>
              <p className="font-serif text-lg">{item.title}</p>
              <p className="mt-1 text-sm text-muted">{item.snippet}</p>
              {item.participants.length > 0 ? (
                <p className="mt-1 text-xs text-muted">{item.participants.join(" · ")}</p>
              ) : null}
              {item.kind === "email" ? (
                <button
                  type="button"
                  disabled={pending}
                  onClick={() => void onAttach(item.external_id)}
                  className="mt-2 text-sm text-spark underline-offset-4 hover:underline"
                >
                  Attach to bundle
                </button>
              ) : null}
            </li>
          ))
        )}
      </ul>
      <Drafts projectId={projectId} title={title} />
    </section>
  );
}

function Drafts({ projectId, title }: { projectId: string; title: string }) {
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [subject, setSubject] = useState(`Re: ${title}`);
  const [body, setBody] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  async function refresh() {
    setDrafts(await listDrafts(projectId));
  }

  useEffect(() => {
    refresh().catch((caught: unknown) => {
      setError(caught instanceof Error ? caught.message : "Could not load drafts");
    });
  }, [projectId]);

  async function onStage(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      await createDraft(projectId, subject, body);
      setBody("");
      await refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not stage the draft");
    }
  }

  async function onCopy(draft: Draft) {
    await navigator.clipboard.writeText(`Subject: ${draft.subject}\n\n${draft.body}`);
    setCopied(draft.id);
  }

  return (
    <div className="mt-6 border-t border-line pt-4">
      <h3 className="font-serif text-xl">Clipboard</h3>
      <p className="mt-1 text-sm text-muted">
        Staged replies stay in this process until you copy them. Sparky does not send them.
      </p>
      <form onSubmit={onStage} className="mt-3 grid gap-2">
        <input
          value={subject}
          onChange={(event) => setSubject(event.target.value)}
          className="border border-line bg-white/70 px-3 py-2 text-sm"
          aria-label="Draft subject"
        />
        <textarea
          value={body}
          onChange={(event) => setBody(event.target.value)}
          rows={4}
          className="border border-line bg-white/70 px-3 py-2 text-sm"
          placeholder="Reply text you will paste yourself"
          aria-label="Draft body"
        />
        <button type="submit" className="justify-self-start bg-ink px-4 py-2 text-sm text-paper">
          Stage reply
        </button>
      </form>
      {error ? <p className="mt-2 text-sm text-bad">{error}</p> : null}
      <ul className="mt-4 space-y-3">
        {drafts.map((draft) => (
          <li key={draft.id} className="border border-line p-3">
            <p className="font-serif">{draft.subject}</p>
            <p className="mt-1 whitespace-pre-wrap text-sm">{draft.body}</p>
            <div className="mt-2 flex gap-3 text-sm">
              <button type="button" onClick={() => void onCopy(draft)} className="text-spark">
                {copied === draft.id ? "Copied" : "Copy"}
              </button>
              <button
                type="button"
                onClick={() => void deleteDraft(draft.id).then(refresh)}
                className="text-bad"
              >
                Discard
              </button>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function ChatPanel({ projectId }: { projectId: string }) {
  const [threadId, setThreadId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [text, setText] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function onSend(event: FormEvent) {
    event.preventDefault();
    setPending(true);
    setError(null);
    try {
      const started = await startChat(projectId, text.trim(), threadId);
      setThreadId(started.thread_id);
      setText("");
      const job = await poll(started.job_id, setStatus);
      if (job.status === "FAILED") {
        setError(job.error ?? "Chat failed");
      }
      setMessages(await listMessages(projectId, started.thread_id));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Chat failed");
    } finally {
      setPending(false);
    }
  }

  return (
    <section className="border border-line bg-white/40 p-4">
      <h2 className="font-serif text-2xl">Project chat</h2>
      <p className="mt-1 text-sm text-muted">
        The fast model can call Sparky tools. It cannot send mail. A green claim still has to come
        from a bundle chunk.
      </p>
      <ul className="mt-4 space-y-3">
        {messages.map((message) => (
          <li key={message.id} className="text-sm">
            <p className="text-xs uppercase tracking-[0.14em] text-muted">
              {message.role}
              {message.tool_name ? ` · ${message.tool_name}` : ""}
            </p>
            <p className="mt-1 whitespace-pre-wrap">{message.content}</p>
            {message.referenced_chunks.length > 0 ? (
              <p className="mt-1 text-xs text-muted">
                Chunks: {message.referenced_chunks.join(", ")}
              </p>
            ) : null}
          </li>
        ))}
      </ul>
      <form onSubmit={onSend} className="mt-4 flex flex-col gap-2">
        <textarea
          value={text}
          onChange={(event) => setText(event.target.value)}
          required
          rows={3}
          className="border border-line bg-white/70 px-3 py-2 text-sm"
          placeholder="Ask about this project"
        />
        <button
          type="submit"
          disabled={pending}
          className="justify-self-start bg-spark px-4 py-2 text-sm text-white disabled:opacity-60"
        >
          {pending ? "Working…" : "Send"}
        </button>
      </form>
      {status ? <p className="mt-2 text-sm text-muted">{status}</p> : null}
      {error ? <p className="mt-2 text-sm text-bad">{error}</p> : null}
    </section>
  );
}

async function poll(id: string, onStatus: (message: string) => void) {
  const deadline = Date.now() + 180000;
  while (Date.now() < deadline) {
    const job = await getJob(id);
    onStatus(job.progress_message || job.status);
    if (job.status === "DONE" || job.status === "FAILED" || job.status === "CANCELLED") {
      return job;
    }
    await new Promise((resolve) => setTimeout(resolve, 400));
  }
  throw new Error("The reply is still running. Refresh in a moment.");
}
