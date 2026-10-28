"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import {
  listBundles,
  listEvaluations,
  pollJob,
  startEvaluation,
  type Bundle,
  type Evaluation,
} from "@/lib/knowledge";

const LIGHT_CLASS = {
  GREEN: "bg-ok",
  YELLOW: "bg-warn",
  RED: "bg-bad",
};

export function EvaluationPanel({ projectId }: { projectId: string }) {
  const [bundles, setBundles] = useState<Bundle[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [evaluation, setEvaluation] = useState<Evaluation | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    Promise.all([listBundles(), listEvaluations(projectId)])
      .then(([found, history]) => {
        setBundles(found);
        setSelected(found.map((bundle) => bundle.id));
        setEvaluation(history[0] ?? null);
      })
      .catch((caught: unknown) => {
        setError(caught instanceof Error ? caught.message : "Could not load matches");
      });
  }, [projectId]);

  async function onRun() {
    if (selected.length === 0) {
      setError("Select at least one bundle.");
      return;
    }
    setPending(true);
    setError(null);
    setStatus("Queued");
    try {
      const jobId = await startEvaluation(projectId, selected);
      const job = await pollJob(jobId, setStatus);
      if (job.status === "FAILED") {
        setError(job.error ?? "Evaluation failed");
      } else {
        const history = await listEvaluations(projectId);
        setEvaluation(history[0] ?? null);
        setStatus("Scored.");
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Evaluation failed");
    } finally {
      setPending(false);
    }
  }

  const hazards =
    evaluation?.lexical_report.terms.filter((term) => term.filter_hazard).map((term) => term.term) ??
    [];

  return (
    <section className="border border-line bg-white/50 p-4">
      <h2 className="font-serif text-2xl">Match</h2>
      <p className="mt-1 text-sm leading-6 text-muted">
        A lexical pass shows what an ATS parser would count. The heavy model then colours each
        requirement and must cite a retrieved chunk for green.{" "}
        <Link href="/bundles" className="underline-offset-4 hover:underline">
          Bundles
        </Link>{" "}
        are the only evidence.
      </p>
      <div className="mt-3 flex flex-col gap-1">
        {bundles.length === 0 ? (
          <p className="text-sm text-muted">No bundles yet.</p>
        ) : (
          bundles.map((bundle) => (
            <label key={bundle.id} className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={selected.includes(bundle.id)}
                onChange={(event) =>
                  setSelected((current) =>
                    event.target.checked
                      ? [...current, bundle.id]
                      : current.filter((id) => id !== bundle.id),
                  )
                }
              />
              {bundle.name}
            </label>
          ))
        )}
      </div>
      <button
        type="button"
        onClick={onRun}
        disabled={pending}
        className="mt-3 bg-spark px-4 py-2 text-sm text-white disabled:opacity-60"
      >
        {pending ? "Working…" : "Run evaluation"}
      </button>
      {status ? <p className="mt-2 text-sm text-muted">{status}</p> : null}
      {error ? <p className="mt-2 text-sm text-bad">{error}</p> : null}

      {evaluation ? (
        <div className="mt-5">
          <p className="font-serif text-3xl">{evaluation.match_percentage.toFixed(0)}%</p>
          <p className="text-xs text-muted">
            {evaluation.model_name} · {evaluation.prompt_version}
            {evaluation.classification.profile_skills_used.length
              ? ` · confirmed skills: ${evaluation.classification.profile_skills_used.join(", ")}`
              : ""}
          </p>
          {hazards.length > 0 ? (
            <p className="mt-2 text-sm text-bad">
              Filter hazard, mandatory terms with no lexical hit: {hazards.join(", ")}
            </p>
          ) : null}
          <ul className="mt-4 space-y-3">
            {evaluation.classification.items.map((item) => (
              <li key={`${item.group}-${item.term}`} className="border border-line bg-white p-3">
                <div className="flex items-center gap-2">
                  <span className={`inline-block h-2.5 w-2.5 rounded-full ${LIGHT_CLASS[item.light]}`} />
                  <span className="text-sm font-medium">{item.term}</span>
                  <span className="text-xs uppercase tracking-wide text-muted">{item.group}</span>
                  {item.citation_downgraded ? (
                    <span className="text-xs text-warn">downgraded, citation missing</span>
                  ) : null}
                </div>
                <p className="mt-1 text-sm leading-6">{item.rationale}</p>
                {item.citations.length > 0 ? (
                  <ul className="mt-2 space-y-2">
                    {item.citations.map((citation) => (
                      <li key={citation.chunk_id} className="bg-paper px-2 py-2 text-xs leading-5">
                        {citation.missing ? (
                          <span className="text-muted">Chunk {citation.chunk_id} is gone.</span>
                        ) : (
                          <>
                            <span className="text-muted">
                              {citation.filename}
                              {citation.section_header ? ` · ${citation.section_header}` : ""}
                            </span>
                            <span className="mt-1 block">{citation.excerpt}</span>
                          </>
                        )}
                      </li>
                    ))}
                  </ul>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
