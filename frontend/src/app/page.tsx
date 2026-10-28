import { HealthPanel } from "@/components/HealthPanel";

const LOOPS = [
  {
    title: "Target job",
    detail: "Paste a Profesia link or posting on Projects. The fast model structures it.",
  },
  {
    title: "Knowledge bundles",
    detail: "Upload CVs and notes, or drop files in the inbox. They are the only evidence.",
  },
  {
    title: "Match",
    detail: "Open a project, pick bundles, and read the cited traffic lights.",
  },
  {
    title: "Documents",
    detail: "Typst CV and cover letter, EN or SK, with a round-trip check. Slice 3.",
  },
  {
    title: "Timeline",
    detail: "Read-only Gmail and Calendar, drafts you send yourself. Slice 4.",
  },
];

export default function HomePage() {
  return (
    <div className="max-w-3xl">
      <p className="text-xs uppercase tracking-[0.18em] text-spark">Slice 2</p>
      <h1 className="mt-2 font-serif text-4xl tracking-tight">Match against what you have done.</h1>
      <p className="mt-3 max-w-xl text-base leading-7 text-muted">
        Ingest a posting, keep your history in bundles, and run a cited traffic-light evaluation.
        The score is computed here. The model does not invent a number, and a green claim needs a
        chunk from your bundles.
      </p>
      <div className="mt-8">
        <HealthPanel />
      </div>
      <ol className="mt-10 grid gap-3 sm:grid-cols-2">
        {LOOPS.map((loop, index) => (
          <li key={loop.title} className="border border-line bg-white/40 p-4">
            <p className="text-xs text-muted">0{index + 1}</p>
            <h2 className="mt-1 font-serif text-lg">{loop.title}</h2>
            <p className="mt-1 text-sm leading-6 text-muted">{loop.detail}</p>
          </li>
        ))}
      </ol>
    </div>
  );
}
