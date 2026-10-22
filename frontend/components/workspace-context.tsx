"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api } from "@/lib/api";
import { cleanFilters } from "@/lib/format";
import type { Comparison, Filters, Investigation, Meta } from "@/lib/types";

export type Stage = "detect" | "diagnose" | "challenge" | "decide" | "measure" | "data";

interface Ctx {
  meta: Meta;
  filters: Filters;
  setFilters: (f: Filters) => void;
  comparison: Comparison;
  setComparison: (c: Comparison) => void;
  stage: Stage;
  setStage: (s: Stage) => void;
  investigation: Investigation | null;
  setInvestigation: (i: Investigation | null) => void;
  investigating: boolean;
  investigationError: string | null;
  runInvestigation: (question: string, focus?: { metric: string; filters: Filters }) => Promise<void>;
  useLlm: boolean;
  setUseLlm: (v: boolean) => void;
  version: number;
}

const WorkspaceContext = createContext<Ctx | null>(null);

export function WorkspaceProvider({ meta, children }: { meta: Meta; children: ReactNode }) {
  const [filters, setFiltersRaw] = useState<Filters>({});
  const [comparison, setComparison] = useState<Comparison>(meta.default_comparison);
  const [stage, setStage] = useState<Stage>("detect");
  const [investigation, setInvestigation] = useState<Investigation | null>(null);
  const [investigating, setInvestigating] = useState(false);
  const [investigationError, setInvestigationError] = useState<string | null>(null);
  const [useLlm, setUseLlm] = useState(meta.llm.enabled);
  const [version, setVersion] = useState(0);
  const reqId = useRef(0);

  const setFilters = useCallback((f: Filters) => {
    setFiltersRaw(cleanFilters(f));
    setVersion((v) => v + 1);
  }, []);

  const runInvestigation = useCallback(async (question: string, focus?: { metric: string; filters: Filters }) => {
    const id = ++reqId.current;
    setInvestigating(true);
    setInvestigationError(null);
    setStage("challenge");
    try {
      const inv = await api<Investigation>("/investigations", {
        question, comparison, use_llm: useLlm,
        filters: Object.keys(filters).length ? filters : null,
        focus: focus ? { metric: focus.metric, filters: cleanFilters(focus.filters) } : null,
      });
      if (id !== reqId.current) return;
      setInvestigation(inv);
      // The investigation becomes the workspace context: every stage now looks at its focus segment.
      if (inv.finding) setFiltersRaw(cleanFilters(inv.finding.filters));
      setVersion((v) => v + 1);
    } catch (e) {
      if (id === reqId.current) setInvestigationError((e as Error).message);
    } finally {
      if (id === reqId.current) setInvestigating(false);
    }
  }, [comparison, filters, useLlm]);

  useEffect(() => {
    try {
      const saved = window.localStorage.getItem("fd.stage");
      if (saved) setStage(saved as Stage);
    } catch { /* storage unavailable: keep default */ }
  }, []);
  useEffect(() => {
    try { window.localStorage.setItem("fd.stage", stage); } catch { /* ignore */ }
  }, [stage]);

  const value = useMemo<Ctx>(() => ({
    meta, filters, setFilters, comparison, setComparison: (c) => { setComparison(c); setVersion((v) => v + 1); },
    stage, setStage, investigation, setInvestigation, investigating, investigationError, runInvestigation,
    useLlm, setUseLlm, version,
  }), [meta, filters, setFilters, comparison, stage, investigation, investigating, investigationError,
    runInvestigation, useLlm, version]);

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}

export function useWorkspace(): Ctx {
  const ctx = useContext(WorkspaceContext);
  if (!ctx) throw new Error("useWorkspace must be used inside WorkspaceProvider");
  return ctx;
}

/** Fetch helper bound to the workspace version, so every view refetches when filters/windows change. */
export function useApi<T>(path: string | null, body?: unknown, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [nonce, setNonce] = useState(0);
  const key = JSON.stringify(body ?? null);
  useEffect(() => {
    if (!path) return;
    let alive = true;
    setLoading(true);
    setError(null);
    api<T>(path, body)
      .then((d) => { if (alive) setData(d); })
      .catch((e) => { if (alive) setError((e as Error).message); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, key, nonce, ...deps]);
  return { data, error, loading, reload: () => setNonce((n) => n + 1) };
}
