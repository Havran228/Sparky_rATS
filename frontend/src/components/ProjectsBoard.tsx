"use client";

import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";

import { deleteProject, getJob, listProjects, startIngest, type Project } from "@/lib/projects";

export function ProjectsBoard() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [input, setInput] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function refresh() {
    setProjects(await listProjects());
  }

  useEffect(() => {
    refresh().catch((caught: unknown) => {
      setError(caught instanceof Error ? caught.message : "Could not load projects");
    });
  }, []);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setPending(true);
    setError(null);
    setStatus("Queued");
    try {
      const jobId = await startIngest(input.trim());
      const result = await pollJob(jobId, setStatus);
      if (result.status === "FAILED") {
        setError(result.error ?? "Ingest failed");
      } else {
        setInput("");
        await refresh();
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Ingest failed");
    } finally {
      setPending(false);
    }
  }

  async function onDelete(id: string) {
    setPending(true);
    setError(null);
    try {
      await deleteProject(id);
      await refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not delete the project");
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="max-w-3xl">
      <h1 className="font-serif text-3xl">Projects</h1>
      <p className="mt-2 text-sm leading-6 text-muted">
        Paste a Profesia link, a seven-digit id, or the posting text. The fast model turns it into
        a project. CVs and photos go into a knowledge bundle, not this bar.
      </p>
      <form onSubmit={onSubmit} className="mt-5 flex flex-col gap-2 sm:flex-row">
        <input
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder="https://www.profesia.sk/praca/…/O1234567"
          className="min-w-0 flex-1 border border-line bg-white/70 px-3 py-2 text-sm"
          required
        />
        <button
          type="submit"
          disabled={pending}
          className="bg-spark px-4 py-2 text-sm text-white disabled:opacity-60"
        >
          {pending ? "Working…" : "Ingest"}
        </button>
      </form>
      {status ? <p className="mt-2 text-sm text-muted">{status}</p> : null}
      {error ? <p className="mt-2 text-sm text-bad">{error}</p> : null}
      <ul className="mt-8 divide-y divide-line border border-line bg-white/50">
        {projects.length === 0 ? (
          <li className="px-4 py-6 text-sm text-muted">No projects yet.</li>
        ) : (
          projects.map((project) => (
            <li key={project.id} className="flex items-start justify-between gap-3 px-4 py-3">
              <Link href={`/projects/${project.id}`} className="min-w-0 flex-1 hover:opacity-80">
                <span className="font-serif text-lg">{project.title || "Untitled posting"}</span>
                <span className="mt-1 block text-sm text-muted">
                  {[project.company, project.contract_type, salaryLabel(project)]
                    .filter(Boolean)
                    .join(" · ")}
                </span>
              </Link>
              <button
                type="button"
                onClick={() => onDelete(project.id)}
                disabled={pending}
                className="mt-1 shrink-0 text-sm text-bad underline-offset-4 hover:underline disabled:opacity-60"
              >
                Delete
              </button>
            </li>
          ))
        )}
      </ul>
    </div>
  );
}

function salaryLabel(project: Project): string | null {
  const salary = project.salary;
  if (!salary) {
    return null;
  }
  if (salary.minimum && salary.maximum) {
    return `${salary.minimum}–${salary.maximum} ${salary.currency}`;
  }
  if (salary.minimum) {
    return `${salary.minimum} ${salary.currency}`;
  }
  return salary.raw || null;
}

async function pollJob(id: string, onStatus: (message: string) => void) {
  const deadline = Date.now() + 120000;
  while (Date.now() < deadline) {
    const job = await getJob(id);
    onStatus(job.progress_message || job.status);
    if (job.status === "DONE" || job.status === "FAILED" || job.status === "CANCELLED") {
      return job;
    }
    await new Promise((resolve) => setTimeout(resolve, 400));
  }
  throw new Error("Ingest is still running. Refresh the list in a moment.");
}
