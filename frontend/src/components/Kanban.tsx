"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { getBoard, moveProject, type BoardColumn } from "@/lib/slice4";

export function Kanban() {
  const [columns, setColumns] = useState<BoardColumn[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function refresh() {
    setColumns(await getBoard());
  }

  useEffect(() => {
    refresh().catch((caught: unknown) => {
      setError(caught instanceof Error ? caught.message : "Could not load the board");
    });
  }, []);

  async function move(projectId: string, column: string) {
    setPending(true);
    setError(null);
    try {
      await moveProject(projectId, column);
      await refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not move the project");
    } finally {
      setPending(false);
    }
  }

  return (
    <div>
      {error ? <p className="mb-3 text-sm text-bad">{error}</p> : null}
      <div className="flex gap-3 overflow-x-auto pb-2">
        {columns.map((column) => (
          <section
            key={column.status}
            className="w-56 shrink-0 border border-line bg-white/40"
            onDragOver={(event) => event.preventDefault()}
            onDrop={(event) => {
              const projectId = event.dataTransfer.getData("text/project-id");
              if (projectId) void move(projectId, column.status);
            }}
          >
            <header className="border-b border-line px-3 py-2">
              <h2 className="font-serif text-base">{column.label}</h2>
              <p className="text-xs text-muted">{column.projects.length}</p>
            </header>
            <ul className="min-h-24 space-y-2 p-2">
              {column.projects.map((project) => (
                <li
                  key={project.id}
                  draggable
                  onDragStart={(event) => event.dataTransfer.setData("text/project-id", project.id)}
                  className="border border-line bg-paper p-2"
                >
                  <Link href={`/projects/${project.id}`} className="block hover:opacity-80">
                    <span className="font-serif text-sm">{project.title || "Untitled"}</span>
                    <span className="mt-1 block text-xs text-muted">
                      {project.company || "Company not stated"}
                    </span>
                  </Link>
                  <label className="mt-2 block text-xs text-muted">
                    Move
                    <select
                      className="mt-1 w-full border border-line bg-white px-1 py-1 text-xs"
                      value={column.status}
                      disabled={pending}
                      onChange={(event) => void move(project.id, event.target.value)}
                    >
                      {columns.map((option) => (
                        <option key={option.status} value={option.status}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </label>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </div>
  );
}
