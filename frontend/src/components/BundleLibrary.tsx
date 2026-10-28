"use client";

import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";

import {
  DOC_TYPES,
  createBundle,
  deleteBundle,
  getBundle,
  listBundles,
  pollJob,
  uploadDocument,
  type Bundle,
} from "@/lib/knowledge";

export function BundleLibrary() {
  const [bundles, setBundles] = useState<Bundle[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<Bundle | null>(null);
  const [name, setName] = useState("");
  const [kind, setKind] = useState<Bundle["kind"]>("historical_baseline");
  const [docType, setDocType] = useState("CV");
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function refreshList(keep?: string) {
    const found = await listBundles();
    setBundles(found);
    const next = keep && found.some((bundle) => bundle.id === keep) ? keep : found[0]?.id ?? null;
    setSelected(next);
  }

  useEffect(() => {
    refreshList().catch((caught: unknown) => {
      setError(caught instanceof Error ? caught.message : "Could not load bundles");
    });
  }, []);

  useEffect(() => {
    if (!selected) {
      setDetail(null);
      return;
    }
    getBundle(selected)
      .then(setDetail)
      .catch((caught: unknown) => {
        setError(caught instanceof Error ? caught.message : "Could not load the bundle");
      });
  }, [selected]);

  async function onCreate(event: FormEvent) {
    event.preventDefault();
    setPending(true);
    setError(null);
    try {
      const created = await createBundle({ name: name.trim(), kind });
      setName("");
      await refreshList(created.id);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not create the bundle");
    } finally {
      setPending(false);
    }
  }

  async function onUpload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected) {
      return;
    }
    const form = event.currentTarget;
    const input = form.elements.namedItem("file");
    const file = input instanceof HTMLInputElement ? input.files?.[0] : undefined;
    if (!file) {
      return;
    }
    setPending(true);
    setError(null);
    setStatus("Queued");
    try {
      const jobId = await uploadDocument(selected, file, docType);
      const job = await pollJob(jobId, setStatus);
      if (job.status === "FAILED") {
        setError(job.error ?? "Ingest failed");
      } else {
        const duplicate = job.result?.duplicate === true;
        setStatus(duplicate ? "Already stored. Duplicate hash skipped." : "Stored.");
        form.reset();
        setDetail(await getBundle(selected));
        await refreshList(selected);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Upload failed");
    } finally {
      setPending(false);
    }
  }

  async function onDelete() {
    if (!selected) {
      return;
    }
    setPending(true);
    setError(null);
    try {
      await deleteBundle(selected);
      await refreshList();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not delete the bundle");
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="max-w-3xl">
      <h1 className="font-serif text-3xl">Bundles</h1>
      <p className="mt-2 text-sm leading-6 text-muted">
        A bundle is the evidence about you: CVs, letters, postings, notes. Dropping a file in the
        inbox data folder also lands here, in the Inbox bundle. Text and Markdown parse on this
        machine. PDF and DOCX need the optional Docling extra. Photos go to the vision model.
      </p>

      <form onSubmit={onCreate} className="mt-6 flex flex-col gap-2 sm:flex-row">
        <input
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="Bundle name"
          required
          className="min-w-0 flex-1 border border-line bg-white/70 px-3 py-2 text-sm"
        />
        <select
          value={kind}
          onChange={(event) => setKind(event.target.value as Bundle["kind"])}
          className="border border-line bg-white/70 px-3 py-2 text-sm"
        >
          <option value="historical_baseline">Historical baseline</option>
          <option value="active_application">Active application</option>
        </select>
        <button type="submit" disabled={pending} className="bg-spark px-4 py-2 text-sm text-white">
          Create
        </button>
      </form>

      <div className="mt-6 flex flex-wrap gap-2">
        {bundles.length === 0 ? <p className="text-sm text-muted">No bundles yet.</p> : null}
        {bundles.map((bundle) => (
          <button
            key={bundle.id}
            type="button"
            onClick={() => setSelected(bundle.id)}
            className={`border px-3 py-2 text-left text-sm ${
              bundle.id === selected ? "border-spark bg-white" : "border-line bg-white/40"
            }`}
          >
            <span className="block font-serif">{bundle.name}</span>
            <span className="text-xs text-muted">
              {bundle.kind === "active_application" ? "Active" : "Baseline"} ·{" "}
              {bundle.document_count ?? 0} files
            </span>
          </button>
        ))}
      </div>

      {detail ? (
        <section className="mt-8 border border-line bg-white/50 p-4">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h2 className="font-serif text-2xl">{detail.name}</h2>
              <p className="text-sm text-muted">
                {detail.documents?.length ?? 0} documents. Confirmed skills live on the{" "}
                <Link href="/profile" className="underline-offset-4 hover:underline">
                  profile
                </Link>
                .
              </p>
            </div>
            <button
              type="button"
              onClick={onDelete}
              disabled={pending}
              className="text-sm text-bad underline-offset-4 hover:underline"
            >
              Delete
            </button>
          </div>
          <form onSubmit={onUpload} className="mt-4 flex flex-col gap-2 sm:flex-row sm:items-center">
            <input name="file" type="file" required className="text-sm" />
            <select
              value={docType}
              onChange={(event) => setDocType(event.target.value)}
              className="border border-line bg-white px-2 py-2 text-sm"
            >
              {DOC_TYPES.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
            <button
              type="submit"
              disabled={pending}
              className="bg-ink px-4 py-2 text-sm text-paper disabled:opacity-60"
            >
              Upload
            </button>
          </form>
          {status ? <p className="mt-2 text-sm text-muted">{status}</p> : null}
          <ul className="mt-4 divide-y divide-line">
            {(detail.documents ?? []).length === 0 ? (
              <li className="py-3 text-sm text-muted">Nothing stored yet.</li>
            ) : (
              detail.documents?.map((document) => (
                <li key={document.id} className="py-3 text-sm">
                  <span className="font-medium">{document.filename}</span>
                  <span className="mt-1 block text-muted">
                    {document.doc_type}
                    {document.language ? ` · ${document.language}` : ""} · {document.chunk_count}{" "}
                    chunks
                  </span>
                </li>
              ))
            )}
          </ul>
        </section>
      ) : null}
      {error ? <p className="mt-4 text-sm text-bad">{error}</p> : null}
    </div>
  );
}
