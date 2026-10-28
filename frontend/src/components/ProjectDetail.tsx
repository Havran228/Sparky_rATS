"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { EvaluationPanel } from "@/components/EvaluationPanel";
import { SalaryPanel } from "@/components/SalaryPanel";
import { getProject, type Project } from "@/lib/projects";

export function ProjectDetail({ id }: { id: string }) {
  const [project, setProject] = useState<Project | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getProject(id)
      .then(setProject)
      .catch((caught: unknown) => {
        setError(caught instanceof Error ? caught.message : "Project not found");
      });
  }, [id]);

  if (error) {
    return <p className="text-sm text-bad">{error}</p>;
  }
  if (!project) {
    return <p className="text-sm text-muted">Loading project…</p>;
  }

  const salary = project.salary;
  const monthlyGross =
    salary && salary.period === "month" && salary.basis !== "net" ? salary.minimum ?? "" : "";
  const note = publishedNote(project);

  return (
    <div className="max-w-3xl">
      <Link href="/projects" className="text-sm text-muted underline-offset-4 hover:underline">
        All projects
      </Link>
      <p className="mt-4 text-xs uppercase tracking-[0.16em] text-spark">
        {project.contract_type || "Contract unknown"}
        {project.language ? ` · ${project.language}` : ""}
      </p>
      <h1 className="mt-1 font-serif text-4xl tracking-tight">
        {project.title || "Untitled posting"}
      </h1>
      <p className="mt-2 text-muted">{project.company || "Company not stated"}</p>
      {project.source_ref ? (
        <p className="mt-2 break-all text-sm text-muted">{project.source_ref}</p>
      ) : null}

      <div className="mt-8 grid gap-6">
        <RequirementList title="Required" items={project.requirements?.mandatory ?? []} />
        <RequirementList title="Preferred" items={project.requirements?.preferred ?? []} />
        <RequirementList title="Offered" items={project.offerings ?? []} />
        <EvaluationPanel projectId={project.id} />
      </div>

      <div className="mt-8">
        <SalaryPanel
          initialGross={monthlyGross}
          initialContract={
            project.contract_type === "SZCO" || project.contract_type === "DOHODA"
              ? project.contract_type
              : "TPP"
          }
          note={note}
        />
      </div>
    </div>
  );
}

function RequirementList({ title, items }: { title: string; items: string[] }) {
  return (
    <section>
      <h2 className="font-serif text-xl">{title}</h2>
      {items.length === 0 ? (
        <p className="mt-2 text-sm text-muted">None extracted.</p>
      ) : (
        <ul className="mt-2 list-disc space-y-1 pl-5 text-sm">
          {items.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      )}
    </section>
  );
}

function publishedNote(project: Project): string | null {
  const salary = project.salary;
  if (!salary) {
    return null;
  }
  if (salary.basis === "net") {
    return `The posting publishes a net figure${salary.raw ? `: ${salary.raw}` : ""}. It is not converted.`;
  }
  if (salary.period === "hour") {
    return `The posting publishes an hourly figure${salary.minimum ? ` (${salary.minimum} ${salary.currency})` : ""}. Enter a monthly gross to estimate take-home.`;
  }
  if (project.contract_type === "SZCO" || project.contract_type === "DOHODA") {
    return "This contract is outside the employment model. The calculator will leave the figure unconverted.";
  }
  return salary.raw ? `Published salary: ${salary.raw}` : null;
}
