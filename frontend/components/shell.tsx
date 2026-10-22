"use client";

import clsx from "clsx";
import { useEffect, useRef, useState } from "react";
import { CATEGORY_LABEL, filtersLabel, humanize, pp, pval } from "@/lib/format";
import type { Dimension, Filters } from "@/lib/types";
import { Button, StatusBadge } from "./ui";
import { type Stage, useWorkspace } from "./workspace-context";

export const STAGES: { key: Stage; n?: string; label: string; hint: string }[] = [
  { key: "detect", n: "1", label: "Detect", hint: "What changed" },
  { key: "diagnose", n: "2", label: "Diagnose", hint: "Where it changed" },
  { key: "challenge", n: "3", label: "Challenge", hint: "Why — competing explanations" },
  { key: "decide", n: "4", label: "Decide", hint: "Experiment & impact" },
  { key: "measure", n: "5", label: "Measure", hint: "Read out results" },
  { key: "data", label: "Data & SQL", hint: "Get your own data" },
];

export function Header() {
  const { meta, useLlm, setUseLlm } = useWorkspace();
  return (
    <header className="border-b border-line bg-raised">
      <div className="mx-auto flex max-w-[1400px] flex-wrap items-center justify-between gap-3 px-4 py-3 md:px-6">
        <div className="flex items-center gap-3">
          <div aria-hidden className="flex h-7 w-7 items-center justify-center rounded-md bg-ink text-[13px] font-bold text-surface">FD</div>
          <div>
            <h1 className="text-[15px] font-semibold tracking-tight">Funnel Detective</h1>
            <p className="text-2xs text-ink-3">Investigate why property seekers don&apos;t convert — and decide what to do next</p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="rounded border border-warn/40 bg-warn/5 px-2 py-0.5 font-medium text-warn" title={meta.disclaimer}>
            Synthetic data · independent portfolio project
          </span>
          {meta.llm.enabled ? (
            <label className="flex items-center gap-1.5 rounded border border-line px-2 py-0.5 text-ink-2">
              <input type="checkbox" checked={useLlm} onChange={(e) => setUseLlm(e.target.checked)} />
              LLM interpretation ({meta.llm.model})
            </label>
          ) : (
            <span className="rounded border border-line px-2 py-0.5 text-ink-2" title="No ANTHROPIC_API_KEY configured: question routing and narratives use deterministic templates. All numbers come from the same analytics either way.">
              Deterministic mode (no LLM key)
            </span>
          )}
        </div>
      </div>
    </header>
  );
}

export function AskBar() {
  const { meta, runInvestigation, investigating } = useWorkspace();
  const [q, setQ] = useState("");
  return (
    <div className="rounded-lg border border-line bg-raised p-4">
      <form className="flex flex-col gap-2 sm:flex-row" onSubmit={(e) => { e.preventDefault(); if (q.trim().length >= 3) runInvestigation(q.trim()); }}>
        <label htmlFor="ask" className="sr-only">Business question</label>
        <input id="ask" value={q} onChange={(e) => setQ(e.target.value)} maxLength={500}
          placeholder="Ask a business question, e.g. “Why did mobile contact conversion decline in Riyadh?”"
          className="min-w-0 flex-1 rounded-md border border-line bg-surface px-3 py-2 text-[14px] placeholder:text-ink-3 focus:border-accent focus:outline-none" />
        <Button type="submit" variant="primary" disabled={investigating || q.trim().length < 3}>
          {investigating ? "Investigating…" : "Investigate"}
        </Button>
      </form>
      <div className="mt-3 flex flex-wrap gap-1.5">
        {meta.example_questions.map((ex) => (
          <button key={ex} onClick={() => { setQ(ex); runInvestigation(ex); }} disabled={investigating}
            className="rounded-full border border-line px-2.5 py-1 text-xs text-ink-2 transition-colors hover:border-accent/40 hover:bg-accent-soft hover:text-accent disabled:opacity-50">
            {ex}
          </button>
        ))}
      </div>
    </div>
  );
}

/** The active investigation, shown across every stage so the flow stays connected. */
export function CaseStrip() {
  const { investigation: inv, setStage, stage, setInvestigation, investigating } = useWorkspace();
  if (investigating && !inv) return null;
  if (!inv || !inv.finding) return null;
  const f = inv.finding;
  const lead = inv.hypotheses[0];
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border border-accent/30 bg-accent-soft/60 px-4 py-2.5 text-[13px]">
      <span className="text-2xs font-semibold uppercase tracking-wider text-accent">Active case</span>
      <span className="font-medium text-ink">“{inv.question}”</span>
      <span className="text-ink-2">{f.observed_change.metric_label} · {f.scope.replace(/=/g, ": ")} · <span className="num">{pp(f.observed_change.abs_change_pp)}</span> (p={pval(f.observed_change.p_value)})</span>
      {lead && <span className="flex items-center gap-1.5 text-ink-2">Lead: {CATEGORY_LABEL[lead.category]} <StatusBadge status={lead.status} /></span>}
      <span className="text-ink-2">Confidence: <b className="font-medium text-ink">{f.confidence}</b></span>
      <span className="ml-auto flex gap-1">
        {stage !== "challenge" && <Button variant="ghost" onClick={() => setStage("challenge")}>Hypotheses</Button>}
        {stage !== "decide" && <Button variant="ghost" onClick={() => setStage("decide")}>Experiment</Button>}
        <Button variant="ghost" onClick={() => setInvestigation(null)} title="Close the case (filters stay as they are)">Close</Button>
      </span>
    </div>
  );
}

export function StageNav() {
  const { stage, setStage } = useWorkspace();
  return (
    <nav className="flex gap-1 overflow-x-auto border-b border-line" aria-label="Investigation stages">
      {STAGES.map((s) => (
        <button key={s.key} onClick={() => setStage(s.key)} title={s.hint}
          className={clsx("-mb-px flex shrink-0 items-center gap-2 border-b-2 px-3 py-2.5 text-[13px] font-medium transition-colors",
            stage === s.key ? "border-accent text-ink" : "border-transparent text-ink-3 hover:text-ink")}>
          {s.n && <span className={clsx("flex h-5 w-5 items-center justify-center rounded-full text-2xs",
            stage === s.key ? "bg-accent text-white" : "bg-sunken text-ink-3")}>{s.n}</span>}
          {s.label}
          <span className="hidden text-2xs font-normal text-ink-3 lg:inline">{s.hint}</span>
        </button>
      ))}
    </nav>
  );
}

const FILTER_DIMS: { key: Dimension; label: string }[] = [
  { key: "market", label: "Market" }, { key: "device", label: "Device" }, { key: "channel", label: "Channel" },
  { key: "property_type", label: "Property" }, { key: "price_band", label: "Price band" },
  { key: "intent", label: "Intent" }, { key: "visitor_type", label: "Visitor" },
];

function MultiSelect({ label, values, selected, onChange }: {
  label: string; values: string[]; selected: string[]; onChange: (v: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, []);
  const summary = selected.length === 0 ? "All" : selected.length === 1 ? humanize(selected[0]) : `${selected.length} selected`;
  return (
    <div className="relative" ref={ref}>
      <button onClick={() => setOpen(!open)} aria-expanded={open}
        className={clsx("flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs",
          selected.length ? "border-accent/40 bg-accent-soft text-accent" : "border-line bg-raised text-ink-2 hover:bg-sunken")}>
        <span className="text-ink-3">{label}:</span> <span className="font-medium">{summary}</span> <span aria-hidden>▾</span>
      </button>
      {open && (
        <div className="absolute left-0 z-40 mt-1 min-w-[180px] rounded-md border border-line bg-raised p-1 shadow-lg">
          {values.map((v) => (
            <label key={v} className="flex cursor-pointer items-center gap-2 rounded px-2 py-1 text-xs hover:bg-sunken">
              <input type="checkbox" checked={selected.includes(v)}
                onChange={(e) => onChange(e.target.checked ? [...selected, v] : selected.filter((x) => x !== v))} />
              {humanize(v)}
            </label>
          ))}
          {selected.length > 0 && (
            <button className="mt-1 w-full rounded px-2 py-1 text-left text-xs text-ink-3 hover:bg-sunken" onClick={() => onChange([])}>Clear</button>
          )}
        </div>
      )}
    </div>
  );
}

export function FilterBar() {
  const { meta, filters, setFilters, comparison, setComparison } = useWorkspace();
  const [draft, setDraft] = useState(comparison);
  useEffect(() => setDraft(comparison), [comparison]);
  const valid = draft.baseline.start <= draft.baseline.end && draft.current.start <= draft.current.end &&
    draft.baseline.end < draft.current.start;
  const dateInput = (value: string, onChange: (v: string) => void, label: string) => (
    <input type="date" aria-label={label} value={value} min={meta.date_range.start} max={meta.date_range.end}
      onChange={(e) => onChange(e.target.value)}
      className="rounded border border-line bg-raised px-1.5 py-0.5 text-xs text-ink num" />
  );
  const dirty = JSON.stringify(draft) !== JSON.stringify(comparison);
  return (
    <div className="flex flex-wrap items-center gap-2">
      {FILTER_DIMS.map((d) => (
        <MultiSelect key={d.key} label={d.label} values={meta.dimensions[d.key]} selected={filters[d.key] ?? []}
          onChange={(v) => setFilters({ ...filters, [d.key]: v })} />
      ))}
      {Object.keys(filters).length > 0 && (
        <button className="text-xs text-ink-3 underline-offset-2 hover:text-ink hover:underline" onClick={() => setFilters({})}>Reset segments</button>
      )}
      <div className="ml-auto flex flex-wrap items-center gap-1.5 text-xs text-ink-3">
        <span className="inline-flex items-center gap-1"><span className="h-2 w-2 rounded-sm" style={{ background: "var(--chart-baseline)" }} />Baseline</span>
        {dateInput(draft.baseline.start, (v) => setDraft({ ...draft, baseline: { ...draft.baseline, start: v } }), "Baseline start")}
        –{dateInput(draft.baseline.end, (v) => setDraft({ ...draft, baseline: { ...draft.baseline, end: v } }), "Baseline end")}
        <span className="ml-2 inline-flex items-center gap-1"><span className="h-2 w-2 rounded-sm" style={{ background: "var(--chart-current)" }} />Current</span>
        {dateInput(draft.current.start, (v) => setDraft({ ...draft, current: { ...draft.current, start: v } }), "Current start")}
        –{dateInput(draft.current.end, (v) => setDraft({ ...draft, current: { ...draft.current, end: v } }), "Current end")}
        {dirty && <Button variant="primary" disabled={!valid} onClick={() => setComparison(draft)} className="!py-0.5 !text-xs">Apply</Button>}
        {dirty && !valid && <span className="text-bad">Baseline must end before current starts</span>}
      </div>
      <p className="w-full text-2xs text-ink-3">Scope: <span className="font-medium text-ink-2">{filtersLabel(filters)}</span> — all stages use this scope and these windows.</p>
    </div>
  );
}
