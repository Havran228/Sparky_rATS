"use client";

import { useParams } from "next/navigation";

import { ProjectDetail } from "@/components/ProjectDetail";

export default function ProjectPage() {
  const params = useParams<{ id: string }>();
  const id = Array.isArray(params.id) ? params.id[0] : params.id;
  if (!id) {
    return <p className="text-sm text-muted">Missing project.</p>;
  }
  return <ProjectDetail id={id} />;
}
