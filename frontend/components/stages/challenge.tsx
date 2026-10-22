"use client";

import clsx from "clsx";
import { useState } from "react";
import { Button, Card, Delta, EmptyState, ErrorBox, InfoTip, Pill, QueryPanel, Spinner, StatusBadge } from "@/components/ui";
import { useWorkspace } from "@/components/workspace-context";
import { SimpsonView } from "@/components/stages/diagnose";
import { CATEGORY_LABEL, humanize, int, pct, pp, pval } from "@/lib/format";
import type { Evidence, Hypothesis, Investigation } from "@/lib/types";

const LEVEL_TONE = { descriptive: "neutral", associational: "warn", causal: "good" } as const;

function EvidenceChip({ id, inv, onOpen }: { id: string; inv: Investigation; onOpen: (id: string) => void }) {
  const ev = inv.evidence.find((e) => e.id === id);
  return (
    <button onClick={() => onOpen(id)} title={ev?.statement}
      className="rounded border border-line bg-sunken px-1.5 py-0 font-mono text-2xs text-ink-2 hover:border-accent/50 hover:text-accent">
      {id}
    </button>
  );
}

export function ChallengeView() {
  const { investigation: inv, investigating, investigationError, meta, runInvestigation } = useWorkspace();
  const [openEv, setOpenEv] = useState<string | null>(null);
  if (investigating) return <div className="py-16"><Spinner label="Planning the investigation, running approved tools and testing competing explanations…" /></div>;
  if (investigationError) return <ErrorBox error={investigationError} />;
  if (!inv) {
    return (
      <EmptyState title="No active investigation"
        action={<div className="flex max-w-2xl flex-wrap justify-center gap-1.5">{meta.example_questions.slice(0, 5).map((q) => (
          <Button key={q} onClick={() => runInvestigation(q)}>{q}</Button>))}</div>}>
        Ask a question above, or use “Investigate” on any market, warning or detected segment. Every investigation tests
        five competing explanations against the data before suggesting what to do.
      </EmptyState>
    );
  }
  const open = (id: string) => {
    setOpenEv(id);
    requestAnimationFrame(() => document.getElementById(`ev-${id}`)?.scrollIntoView({ behavior: "smooth", block: "center" }));
  };
  return (
    <div className="space-y-5">
      <FindingCard inv={inv} onOpen={open} />
      {(inv.extras.related_findings?.length ?? 0) > 0 && (
        <Card title="Also found in this investigation" subtitle="Other segments the workflow examined, each with the full hypothesis battery">
          <ul className="grid gap-2 md:grid-cols-2">
            {inv.extras.related_findings!.map((r) => (
              <li key={r.scope + r.metric} className="flex items-center justify-between gap-3 rounded-md border border-line p-3">
                <div className="min-w-0">
                  <p className="text-[13px] font-medium">{r.scope.replace(/=/g, ": ")}</p>
                  <p className="text-xs text-ink-3">{r.metric_label} <span className="num">{pp(r.abs_change_pp)}</span> · p={pval(r.p_value)}</p>
                  <p className="mt-1 flex items-center gap-1.5 text-xs">{CATEGORY_LABEL[r.leading_category]} <StatusBadge status={r.leading_status} /></p>
                </div>
                <Button onClick={() => runInvestigation(`Why did ${r.metric_label} change for ${r.scope}?`, { metric: r.metric, filters: r.filters })}>Open</Button>
              </li>
            ))}
          </ul>
        </Card>
      )}
      <div>
        <div className="mb-3 flex items-baseline justify-between">
          <h2 className="text-[15px] font-semibold">Competing explanations</h2>
          <InfoTip text={inv.hypotheses[0]?.status_rule ?? ""}><span className="text-xs text-ink-3 underline decoration-dotted">How status is decided</span></InfoTip>
        </div>
        <div className="space-y-3">
          {inv.hypotheses.map((h, i) => <HypothesisCard key={h.id} h={h} inv={inv} onOpen={open} defaultOpen={i === 0} />)}
        </div>
      </div>
      {inv.extras.simpson && (
        <Card title="Simpson's paradox view (by channel)"><SimpsonView s={inv.extras.simpson} /></Card>
      )}
      <EvidenceLedger inv={inv} openId={openEv} />
      <AuditCard inv={inv} />
    </div>
  );
}

function FindingCard({ inv, onOpen }: { inv: Investigation; onOpen: (id: string) => void }) {
  const f = inv.finding;
  const n = inv.narrative;
  if (!f) return <ErrorBox error="The investigation produced no finding." />;
  const o = f.observed_change;
  return (
    <div className="grid gap-5 lg:grid-cols-5">
      <Card className="lg:col-span-2" title="Finding" subtitle={f.business_question}>
        <dl className="space-y-2.5 text-[13px]">
          <div><dt className="text-xs text-ink-3">Metric</dt>
            <dd>{o.metric_label} <InfoTip text={`Numerator: ${o.numerator_label}. Denominator: ${o.denominator_label}.`} /></dd></div>
          <div><dt className="text-xs text-ink-3">Scope</dt><dd>{f.scope.replace(/=/g, ": ")}</dd></div>
          <div><dt className="text-xs text-ink-3">{o.comparison_kind === "group" ? "Comparison" : "Windows"}</dt>
            <dd className="text-ink-2">{o.comparison_kind === "group" ? `${o.current_label} vs ${o.baseline_label} (${f.comparison_window.baseline.split(" to ")[0]} to ${f.comparison_window.current.split(" to ")[1]})`
              : `${f.comparison_window.baseline} vs ${f.comparison_window.current}`}</dd></div>
          <div><dt className="text-xs text-ink-3">Observed</dt>
            <dd className="num">
              <span className="text-ink-2">{o.baseline_label}</span> {pct(o.baseline_rate, 2)} <span className="text-ink-3">({int(o.baseline_n)})</span> →{" "}
              <span className="text-ink-2">{o.current_label}</span> <b className="font-semibold">{pct(o.current_rate, 2)}</b> <span className="text-ink-3">({int(o.current_n)})</span>
              <div className="mt-0.5 flex flex-wrap items-center gap-2"><Delta value={o.abs_change_pp} digits={2} />
                <span className="text-xs text-ink-3">95% CI {o.diff_ci_pp[0].toFixed(2)} to {o.diff_ci_pp[1].toFixed(2)}pp · {o.test} · p={pval(o.p_value)}</span></div>
              {o.channel_adjusted_change_pp !== null && (
                <p className="text-xs text-ink-3">Holding channel mix fixed: {pp(o.channel_adjusted_change_pp, 2)} (p={pval(o.channel_adjusted_p_value)})</p>)}
            </dd></div>
          <div><dt className="text-xs text-ink-3">Confidence</dt>
            <dd className="flex items-center gap-2"><Pill tone={f.confidence === "high" ? "good" : f.confidence === "medium" ? "warn" : "neutral"}>{humanize(f.confidence)}</Pill>
              <InfoTip text={f.confidence_basis} /><span className="text-2xs text-ink-3">rule-based, not a probability</span></dd></div>
        </dl>
        <div className="mt-4 space-y-3 border-t border-line pt-3 text-xs">
          <div><p className="mb-1 font-medium text-ink-2">Alternative explanations</p>
            <ul className="list-disc space-y-0.5 pl-4 text-ink-3">{f.alternative_explanations.map((a) => <li key={a}>{a}</li>)}</ul></div>
          <div><p className="mb-1 font-medium text-ink-2">Limitations</p>
            <ul className="list-disc space-y-0.5 pl-4 text-ink-3">{f.limitations.map((a) => <li key={a}>{a}</li>)}</ul></div>
          <div><p className="mb-1 font-medium text-ink-2">Next analysis to reduce uncertainty</p>
            <ul className="list-disc space-y-0.5 pl-4 text-ink-3">{f.next_analysis.map((a) => <li key={a}>{a}</li>)}</ul></div>
        </div>
      </Card>
      <Card className="lg:col-span-3" title={<span className="flex items-center gap-2">Interpretation
        <Pill tone={n.source === "llm" ? "accent" : "neutral"}>{n.source === "llm" ? `LLM · ${n.model} · validated` : "Deterministic template"}</Pill></span>}
        subtitle="Generated text, kept separate from the evidence. Every point cites the evidence it rests on; numbers are checked against computed results.">
        <p className="text-[14px] leading-relaxed text-ink">{n.summary}</p>
        <ul className="mt-4 space-y-2.5">
          {n.key_points.map((kp, i) => (
            <li key={i} className="flex gap-2.5 text-[13px]">
              <span aria-hidden className={clsx("mt-1 h-2 w-2 shrink-0 rounded-full",
                kp.stance === "supports" ? "bg-good" : kp.stance === "contradicts" ? "bg-ink-3" : "bg-accent")} />
              <span className="text-ink-2">{kp.text} <span className="ml-1 inline-flex gap-1">{kp.evidence_ids.map((id) => <EvidenceChip key={id} id={id} inv={inv} onOpen={onOpen} />)}</span></span>
            </li>
          ))}
        </ul>
        {n.fallback_reason && <p className="mt-3 rounded bg-warn/10 px-2 py-1.5 text-xs text-warn">{n.fallback_reason}</p>}
        {n.validation_issues.length > 0 && (
          <details className="mt-2 text-xs text-ink-3"><summary className="cursor-pointer">Why the LLM text was rejected ({n.validation_issues.length})</summary>
            <ul className="mt-1 list-disc pl-4">{n.validation_issues.map((v) => <li key={v}>{v}</li>)}</ul></details>
        )}
        {inv.planner_notes.length > 0 && (
          <div className="mt-4 border-t border-line pt-3 text-xs text-ink-3">
            {inv.planner_notes.map((p) => <p key={p}>• {p}</p>)}
          </div>
        )}
      </Card>
    </div>
  );
}

const RESULT_STYLE = {
  supports: { icon: "✓", cls: "text-good", label: "Supports" },
  contradicts: { icon: "✕", cls: "text-bad", label: "Contradicts" },
  neutral: { icon: "–", cls: "text-ink-3", label: "Neutral" },
};

function HypothesisCard({ h, inv, onOpen, defaultOpen }: { h: Hypothesis; inv: Investigation; onOpen: (id: string) => void; defaultOpen: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  const s = h.checks.filter((c) => c.result === "supports").length;
  const c = h.checks.filter((c) => c.result === "contradicts").length;
  const comps = h.segment_comparisons.slice(0, 8);
  return (
    <div className={clsx("rounded-lg border bg-raised", h.status === "supported" ? "border-good/40" : "border-line")}>
      <button onClick={() => setOpen(!open)} className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3 text-left" aria-expanded={open}>
        <span className="font-mono text-2xs text-ink-3">{h.id}</span>
        <span className="text-[14px] font-medium">{h.title}</span>
        <StatusBadge status={h.status} />
        <Pill tone={LEVEL_TONE[h.evidence_level]} title={inv.evidence_level_help[h.evidence_level]}>{humanize(h.evidence_level)} evidence</Pill>
        {h.explained_share !== null && h.status !== "not_supported" && (
          <span className="num text-xs text-ink-2" title={h.explained_share_basis ?? ""}>
            accounts for ~{Math.round(h.explained_share * 100)}% of the change
          </span>
        )}
        <span className="num ml-auto text-xs text-ink-3">{s} supporting · {c} contradicting</span>
        <span aria-hidden className="text-ink-3">{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <div className="space-y-4 border-t border-line px-4 py-4">
          <div>
            <p className="text-xs font-medium text-ink-2">If this were true, we would expect…</p>
            <p className="mt-0.5 text-[13px] text-ink-2">{h.predicted_pattern}</p>
          </div>
          <div>
            <p className="mb-1.5 text-xs font-medium text-ink-2">Checks against the evidence</p>
            <ul className="divide-y divide-line rounded-md border border-line">
              {h.checks.map((ch, i) => {
                const st = RESULT_STYLE[ch.result];
                return (
                  <li key={i} className="grid gap-1 px-3 py-2 text-[13px] sm:grid-cols-[110px_1fr]">
                    <span className={clsx("text-xs font-medium", st.cls)}><span aria-hidden>{st.icon}</span> {st.label}</span>
                    <span>
                      <span className="text-ink">{ch.description}</span>
                      <span className="block text-xs text-ink-3">{ch.detail} <span className="ml-1 inline-flex gap-1">{ch.evidence_ids.map((id) => <EvidenceChip key={id} id={id} inv={inv} onOpen={onOpen} />)}</span></span>
                    </span>
                  </li>
                );
              })}
            </ul>
          </div>
          {comps.length > 0 && <Comparisons rows={comps} />}
          <div>
            <p className="text-xs font-medium text-ink-2">What would distinguish it from the alternatives</p>
            <ul className="mt-0.5 list-disc pl-4 text-xs text-ink-3">{h.discriminating_data.map((d) => <li key={d}>{d}</li>)}</ul>
          </div>
          <p className="text-2xs text-ink-3">{inv.evidence_level_help[h.evidence_level]} Correlation in these checks does not prove causation.</p>
        </div>
      )}
    </div>
  );
}

function Comparisons({ rows }: { rows: Record<string, unknown>[] }) {
  const cols = Object.keys(rows[0]).filter((k) => k !== "segment");
  const fmt = (k: string, v: unknown) => {
    if (v === null || v === undefined) return "—";
    if (typeof v === "number") {
      if (k.includes("change_pp")) return pp(v);
      if (k === "q_value") return pval(v);
      return pct(v);
    }
    return String(v);
  };
  return (
    <div>
      <p className="mb-1 text-xs font-medium text-ink-2">Relevant segment comparisons</p>
      <div className="overflow-x-auto">
        <table className="num w-full min-w-[420px] text-xs">
          <thead><tr className="text-left text-ink-3">{"segment" in rows[0] && <th className="pb-1 font-normal">Segment</th>}
            {cols.map((c) => <th key={c} className="pb-1 text-right font-normal">{humanize(c)}</th>)}</tr></thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} className="border-t border-line">
                {"segment" in r && <td className="py-1">{Object.values(r.segment as Record<string, string>).map(humanize).join(" · ")}</td>}
                {cols.map((c) => <td key={c} className="text-right">{fmt(c, r[c])}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function EvidenceLedger({ inv, openId }: { inv: Investigation; openId: string | null }) {
  return (
    <Card title={`Evidence ledger (${inv.evidence.length})`}
      subtitle="Each item was produced by an approved tool; statements are generated from the computed numbers, not by the LLM.">
      <ul className="divide-y divide-line">
        {inv.evidence.map((e: Evidence) => (
          <li key={e.id} id={`ev-${e.id}`} className={clsx("py-2.5 text-[13px] transition-colors", openId === e.id && "-mx-2 rounded bg-accent-soft px-2")}>
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-2xs text-ink-3">{e.id}</span>
              <span className="font-medium">{e.title}</span>
              <Pill tone={LEVEL_TONE[e.evidence_level]} title={inv.evidence_level_help[e.evidence_level]}>{e.evidence_level}</Pill>
              <span className="font-mono text-2xs text-ink-3">{e.tool}</span>
            </div>
            <p className="mt-0.5 text-xs leading-relaxed text-ink-2">{e.statement}</p>
            {e.query_ids.length > 0 && <QueryPanel queries={inv.queries.filter((q) => e.query_ids.includes(q.id))} label="SQL" />}
          </li>
        ))}
      </ul>
    </Card>
  );
}

function AuditCard({ inv }: { inv: Investigation }) {
  return (
    <Card title="How this investigation ran" subtitle={`Investigation ${inv.id} · ${inv.mode === "llm" ? "LLM planner" : "deterministic router"} · ${inv.tool_calls.length} tool calls · ${inv.queries.length} SQL queries · ${inv.extras.duration_ms ?? "?"} ms`}>
      <p className="text-[13px] text-ink-2"><span className="font-medium text-ink">Plan:</span> intent <code className="font-mono text-xs">{inv.plan.intent}</code>, metric <code className="font-mono text-xs">{inv.plan.metric}</code>. {inv.plan.rationale}</p>
      <div className="mt-3 overflow-x-auto">
        <table className="w-full min-w-[640px] text-xs">
          <thead><tr className="text-left text-ink-3"><th className="pb-1 font-normal">#</th><th className="pb-1 font-normal">Tool</th>
            <th className="pb-1 font-normal">Requested by</th><th className="pb-1 font-normal">Status</th><th className="pb-1 font-normal">Evidence</th>
            <th className="pb-1 text-right font-normal">ms</th><th className="pb-1 font-normal">Arguments</th></tr></thead>
          <tbody>
            {inv.tool_calls.map((t, i) => (
              <tr key={t.id} className="border-t border-line align-top">
                <td className="py-1 text-ink-3">{i + 1}</td>
                <td className="font-mono">{t.tool}</td>
                <td className="text-ink-3">{t.requested_by.replace("_", " ")}</td>
                <td>{t.status === "ok" ? <span className="text-good">ok</span> : <span className="text-bad" title={t.error ?? ""}>error</span>}</td>
                <td className="font-mono text-ink-3">{t.evidence_ids.join(", ")}</td>
                <td className="num text-right text-ink-3">{t.duration_ms.toFixed(0)}</td>
                <td><details><summary className="cursor-pointer text-ink-3">view</summary>
                  <pre className="sql mt-1 max-w-[420px] overflow-auto whitespace-pre-wrap rounded bg-sunken p-2">{JSON.stringify(t.args, null, 1)}</pre>
                  {t.error && <p className="text-bad">{t.error}</p>}</details></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <QueryPanel queries={inv.queries} label="All SQL executed" />
    </Card>
  );
}
