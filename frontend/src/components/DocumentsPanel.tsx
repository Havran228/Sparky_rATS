"use client";

import { useEffect, useState } from "react";

import {
  diffDocuments,
  generateDocument,
  listDocuments,
  pdfUrl,
  runDocumentJob,
  submitDocument,
  type GeneratedDocument,
  type JsonChange,
} from "@/lib/documents";
import { listBundles, listEvaluations, type Bundle } from "@/lib/knowledge";

export function DocumentsPanel({ projectId }: { projectId: string }) {
  const [bundles, setBundles] = useState<Bundle[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [documents, setDocuments] = useState<GeneratedDocument[]>([]);
  const [docType, setDocType] = useState<GeneratedDocument["doc_type"]>("CV");
  const [language, setLanguage] = useState<GeneratedDocument["language"]>("en");
  const [useMatch, setUseMatch] = useState(true);
  const [evaluationId, setEvaluationId] = useState<string | null>(null);
  const [markup, setMarkup] = useState("");
  const [activeId, setActiveId] = useState<string | null>(null);
  const [leftId, setLeftId] = useState("");
  const [rightId, setRightId] = useState("");
  const [changes, setChanges] = useState<JsonChange[] | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    Promise.all([listBundles(), listEvaluations(projectId), listDocuments(projectId)])
      .then(([found, history, docs]) => {
        setBundles(found);
        setSelected(found.map((bundle) => bundle.id));
        setEvaluationId(history[0]?.id ?? null);
        setDocuments(docs);
        setActiveId(docs[0]?.id ?? null);
        setLeftId(docs[1]?.id ?? docs[0]?.id ?? "");
        setRightId(docs[0]?.id ?? "");
      })
      .catch((caught: unknown) => {
        setError(caught instanceof Error ? caught.message : "Could not load documents");
      });
  }, [projectId]);

  const active = documents.find((document) => document.id === activeId) ?? null;

  async function refresh(selectId?: string) {
    const docs = await listDocuments(projectId);
    setDocuments(docs);
    const next = selectId ?? docs[0]?.id ?? null;
    setActiveId(next);
    setLeftId(docs[1]?.id ?? docs[0]?.id ?? "");
    setRightId(docs[0]?.id ?? "");
    setChanges(null);
  }

  async function onGenerate(raw: boolean) {
    if (!raw && selected.length === 0) {
      setError("Select at least one bundle so the generator has evidence.");
      return;
    }
    setPending(true);
    setError(null);
    setStatus("Queued");
    try {
      const jobId = await generateDocument(projectId, {
        doc_type: docType,
        language,
        bundle_ids: selected,
        evaluation_id: useMatch ? evaluationId : null,
        raw_markup: raw ? markup : undefined,
      });
      const job = await runDocumentJob(jobId, setStatus);
      const created = job.result?.document_id;
      await refresh(typeof created === "string" ? created : undefined);
      setStatus("Stored.");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Generation failed");
    } finally {
      setPending(false);
    }
  }

  async function onSubmit(documentId: string) {
    setPending(true);
    setError(null);
    setStatus("Copying into the active application bundle");
    try {
      const jobId = await submitDocument(documentId);
      await runDocumentJob(jobId, setStatus);
      await refresh(documentId);
      setStatus("Submitted copy is in the active application bundle.");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Submit failed");
    } finally {
      setPending(false);
    }
  }

  async function onDiff() {
    if (!leftId || !rightId) {
      setError("Pick two versions to compare.");
      return;
    }
    setError(null);
    try {
      setChanges(await diffDocuments(leftId, rightId));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Diff failed");
    }
  }

  return (
    <section className="border border-line bg-white/50 p-4">
      <h2 className="font-serif text-2xl">Documents</h2>
      <p className="mt-1 text-sm leading-6 text-muted">
        The heavy model fills a JSON resume or cover letter. Your confirmed profile supplies the
        header, languages, and the only skills that may appear. Employers that are not in the
        selected bundles are dropped. Typst compiles inside a throwaway folder, then the PDF text
        is matched again so you can see what an ATS parser will read.
      </p>

      <div className="mt-3 flex flex-wrap gap-3 text-sm">
        <label>
          Kind{" "}
          <select
            className="border border-line bg-white px-2 py-1"
            value={docType}
            onChange={(event) => setDocType(event.target.value as GeneratedDocument["doc_type"])}
          >
            <option value="CV">CV</option>
            <option value="COVER_LETTER">Cover letter</option>
          </select>
        </label>
        <label>
          Language{" "}
          <select
            className="border border-line bg-white px-2 py-1"
            value={language}
            onChange={(event) => setLanguage(event.target.value as GeneratedDocument["language"])}
          >
            <option value="en">English</option>
            <option value="sk">Slovak</option>
          </select>
        </label>
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={useMatch && evaluationId !== null}
            disabled={evaluationId === null}
            onChange={(event) => setUseMatch(event.target.checked)}
          />
          Use latest match keywords
        </label>
      </div>

      <div className="mt-3 flex flex-col gap-1">
        {bundles.length === 0 ? (
          <p className="text-sm text-muted">No bundles yet. Upload evidence before generating.</p>
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
        onClick={() => onGenerate(false)}
        disabled={pending}
        className="mt-3 bg-spark px-4 py-2 text-sm text-white disabled:opacity-60"
      >
        {pending ? "Working…" : "Generate"}
      </button>

      <details className="mt-4 text-sm">
        <summary className="cursor-pointer text-muted">Advanced: compile raw Typst</summary>
        <textarea
          className="mt-2 h-28 w-full border border-line bg-white p-2 font-mono text-xs"
          value={markup}
          onChange={(event) => setMarkup(event.target.value)}
          placeholder={'#set text(size: 12pt)\nYour markup stays inside the sandbox.'}
        />
        <button
          type="button"
          onClick={() => onGenerate(true)}
          disabled={pending || markup.trim() === ""}
          className="mt-2 border border-line bg-white px-3 py-1 disabled:opacity-60"
        >
          Compile markup
        </button>
      </details>

      {status ? <p className="mt-2 text-sm text-muted">{status}</p> : null}
      {error ? <p className="mt-2 text-sm text-bad">{error}</p> : null}

      {documents.length > 0 ? (
        <div className="mt-5">
          <h3 className="font-serif text-xl">Versions</h3>
          <ul className="mt-2 space-y-2">
            {documents.map((document) => (
              <li key={document.id} className="flex flex-wrap items-center gap-2 text-sm">
                <button
                  type="button"
                  className="underline-offset-4 hover:underline"
                  onClick={() => setActiveId(document.id)}
                >
                  {label(document)}
                </button>
                {document.is_submitted ? (
                  <span className="text-xs uppercase tracking-wide text-ok">submitted</span>
                ) : (
                  <button
                    type="button"
                    className="text-xs text-spark underline-offset-4 hover:underline"
                    disabled={pending}
                    onClick={() => onSubmit(document.id)}
                  >
                    Mark submitted
                  </button>
                )}
              </li>
            ))}
          </ul>

          {documents.length > 1 ? (
            <div className="mt-4 flex flex-wrap items-end gap-2 text-sm">
              <VersionSelect label="From" documents={documents} value={leftId} onChange={setLeftId} />
              <VersionSelect label="To" documents={documents} value={rightId} onChange={setRightId} />
              <button type="button" className="border border-line bg-white px-3 py-1" onClick={onDiff}>
                Compare JSON
              </button>
            </div>
          ) : null}
          {changes ? <DiffList changes={changes} /> : null}
          {active ? <Preview document={active} /> : null}
        </div>
      ) : null}
    </section>
  );
}

function VersionSelect({
  label,
  documents,
  value,
  onChange,
}: {
  label: string;
  documents: GeneratedDocument[];
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label>
      {label}{" "}
      <select
        className="border border-line bg-white px-2 py-1"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        {documents.map((document) => (
          <option key={document.id} value={document.id}>
            {labelForOption(document)}
          </option>
        ))}
      </select>
    </label>
  );
}

function DiffList({ changes }: { changes: JsonChange[] }) {
  if (changes.length === 0) {
    return <p className="mt-3 text-sm text-muted">These versions match.</p>;
  }
  return (
    <ul className="mt-3 space-y-2">
      {changes.map((change) => (
        <li key={`${change.kind}-${change.path}`} className="bg-paper px-2 py-2 text-xs leading-5">
          <span className="font-medium">{change.kind}</span>{" "}
          <span className="font-mono">{change.path}</span>
          {change.left !== undefined ? (
            <span className="mt-1 block text-muted">from {showValue(change.left)}</span>
          ) : null}
          {change.right !== undefined ? (
            <span className="mt-1 block">to {showValue(change.right)}</span>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

function Preview({ document }: { document: GeneratedDocument }) {
  const report = document.ats_roundtrip_report;
  const missing = report?.mandatory_missing ?? [];
  const grounding = report?.grounding;
  return (
    <div className="mt-4">
      <p className="text-xs text-muted">
        {document.template}
        {document.model_name ? ` · ${document.model_name}` : ""}
        {document.prompt_version ? ` · ${document.prompt_version}` : ""}
        {typeof report?.text_chars === "number" ? ` · ${report.text_chars} text characters` : ""}
      </p>
      {missing.length > 0 ? (
        <p className="mt-2 text-sm text-bad">
          Mandatory terms missing from the PDF text: {missing.join(", ")}
        </p>
      ) : (
        <p className="mt-2 text-sm text-ok">No mandatory term is missing from the PDF text.</p>
      )}
      {grounding && grounding.mode !== "raw_markup" ? (
        <p className="mt-1 text-sm text-muted">
          Dropped before compile: skills {listOrNone(grounding.dropped_skills)}, employers{" "}
          {listOrNone(grounding.dropped_experience)}, schools{" "}
          {listOrNone(grounding.dropped_education)}.
        </p>
      ) : null}
      <iframe
        title={`${label(document)} preview`}
        src={pdfUrl(document.id)}
        className="mt-3 h-[720px] w-full border border-line bg-white"
      />
    </div>
  );
}

function label(document: GeneratedDocument): string {
  const kind = document.doc_type === "COVER_LETTER" ? "Cover letter" : "CV";
  return `${kind} ${document.language.toUpperCase()} v${document.version}`;
}

function labelForOption(document: GeneratedDocument): string {
  return label(document);
}

function listOrNone(values: string[] | undefined): string {
  if (!values || values.length === 0) {
    return "none";
  }
  return values.join(", ");
}

function showValue(value: unknown): string {
  const text = JSON.stringify(value) ?? "";
  return text.length > 180 ? `${text.slice(0, 180)}…` : text;
}
