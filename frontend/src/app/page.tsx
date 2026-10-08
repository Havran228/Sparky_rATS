import { HealthPanel } from "@/components/HealthPanel";

const LOOPS = [
  {
    title: "Target job",
    detail: "Profesia link, id, PDF, or photo becomes a project. Slice 1.",
  },
  {
    title: "Knowledge bundles",
    detail: "Your documents stay the only source of truth. Slice 2.",
  },
  {
    title: "Match",
    detail: "Lexical ATS pass, then a cited traffic-light report. Slice 2.",
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
      <p className="text-xs uppercase tracking-[0.18em] text-spark">Slice 0</p>
      <h1 className="mt-2 font-serif text-4xl tracking-tight">The shell is up.</h1>
      <p className="mt-3 max-w-xl text-base leading-7 text-muted">
        Sparky_rATS runs on this machine. This slice is the database, job runner, local model
        client, and sign-in boundary. Ingestion and matching are not wired yet.
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
