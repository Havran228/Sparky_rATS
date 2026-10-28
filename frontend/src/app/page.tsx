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
    detail: "Typst CV and cover letter, EN or SK, with a round-trip check.",
  },
  {
    title: "Timeline",
    detail: "Read-only Gmail and Calendar. Drafts stay on the clipboard until you send them.",
  },
];

export default function HomePage() {
  return (
    <div className="max-w-3xl">
      <p className="text-xs uppercase tracking-[0.18em] text-spark">Slice 4</p>
      <h1 className="mt-2 font-serif text-4xl tracking-tight">Track the pipeline. Nothing is sent.</h1>
      <p className="mt-3 max-w-xl text-base leading-7 text-muted">
        Move a project on the board, read Gmail and Calendar for that company, and ask the project
        chat to use the local tools. Drafts stay in a clipboard until you paste them yourself.
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
