"use client";

import { useEffect, useState } from "react";
import { AskBar, CaseStrip, FilterBar, Header, StageNav } from "@/components/shell";
import { ChallengeView } from "@/components/stages/challenge";
import { DataView } from "@/components/stages/data";
import { DecideView } from "@/components/stages/decide";
import { DetectView } from "@/components/stages/detect";
import { DiagnoseView } from "@/components/stages/diagnose";
import { MeasureView } from "@/components/stages/measure";
import { ErrorBox, Spinner } from "@/components/ui";
import { WorkspaceProvider, useWorkspace } from "@/components/workspace-context";
import { api } from "@/lib/api";
import type { Meta } from "@/lib/types";

function Stage() {
  const { stage } = useWorkspace();
  switch (stage) {
    case "detect": return <DetectView />;
    case "diagnose": return <DiagnoseView />;
    case "challenge": return <ChallengeView />;
    case "decide": return <DecideView />;
    case "measure": return <MeasureView />;
    case "data": return <DataView />;
  }
}

export default function Page() {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = () => {
    setError(null);
    api<Meta>("/meta").then(setMeta).catch((e) => setError((e as Error).message));
  };
  useEffect(load, []);

  if (error) {
    return (
      <main className="mx-auto max-w-xl px-4 py-24">
        <h1 className="mb-3 text-lg font-semibold">Funnel Detective</h1>
        <ErrorBox error={error} onRetry={load} />
        <p className="mt-3 text-xs text-ink-3">Start the API with <code className="font-mono">uvicorn app.main:app --port 8000</code> from <code className="font-mono">backend/</code>. See the README for troubleshooting.</p>
      </main>
    );
  }
  if (!meta) return <main className="flex min-h-screen items-center justify-center"><Spinner label="Loading workspace…" /></main>;

  return (
    <WorkspaceProvider meta={meta}>
      <Header />
      <main className="mx-auto max-w-[1400px] space-y-4 px-4 py-5 md:px-6">
        <AskBar />
        <CaseStrip />
        <div className="rounded-lg border border-line bg-raised px-4 pt-1">
          <StageNav />
          <div className="py-3"><FilterBar /></div>
        </div>
        <Stage />
        <footer className="border-t border-line pt-4 text-2xs leading-relaxed text-ink-3">{meta.disclaimer}</footer>
      </main>
    </WorkspaceProvider>
  );
}
