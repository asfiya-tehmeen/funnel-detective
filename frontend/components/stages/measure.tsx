"use client";

import clsx from "clsx";
import { useEffect, useState } from "react";
import { IntervalPlot } from "@/components/charts";
import { Button, Card, ErrorBox, Field, Pill, QueryPanel, Segmented, Spinner, inputCls } from "@/components/ui";
import { useApi, useWorkspace } from "@/components/workspace-context";
import { api } from "@/lib/api";
import { int, pct, pp, pval } from "@/lib/format";
import type { QueryRecord, ResultsAnalysis } from "@/lib/types";

const DECISION_STYLE = {
  ship: { label: "Ship", icon: "✓", cls: "border-good/40 bg-good/5 text-good" },
  iterate: { label: "Iterate", icon: "↻", cls: "border-warn/40 bg-warn/5 text-warn" },
  abandon: { label: "Abandon", icon: "✕", cls: "border-bad/40 bg-bad/5 text-bad" },
  investigate: { label: "Investigate data", icon: "⚠", cls: "border-bad/40 bg-bad/5 text-bad" },
};

export function ResultView({ r, mde }: { r: ResultsAnalysis; mde?: number | null }) {
  const d = DECISION_STYLE[r.decision];
  const t = r.test;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <span className={clsx("inline-flex items-center gap-1.5 rounded-md border px-3 py-1 text-[14px] font-semibold", d.cls)}>
          <span aria-hidden>{d.icon}</span>{d.label}
        </span>
        <Pill tone={r.source === "simulated" ? "warn" : "accent"}>{r.outcome_type}</Pill>
        <span className="text-xs text-ink-3">{r.metric_label}</span>
      </div>
      <ul className="list-disc space-y-0.5 pl-4 text-[13px] text-ink-2">{r.reasons.map((x) => <li key={x}>{x}</li>)}</ul>
      <div className="grid gap-4 md:grid-cols-2">
        <div className="rounded-md border border-line p-3">
          <p className="text-xs font-medium text-ink-2">Treatment − control (pp), 95% CI</p>
          <IntervalPlot value={t.abs_change_pp} lo={t.diff_ci_pp[0]} hi={t.diff_ci_pp[1]} mde={mde} />
          <p className="num mt-1 text-xs text-ink-3">{pp(t.abs_change_pp, 2)} ({t.diff_ci_pp[0].toFixed(2)} to {t.diff_ci_pp[1].toFixed(2)}) ·
            relative {r.relative_lift_pct === null ? "—" : `${r.relative_lift_pct.toFixed(1)}%`} · {t.test} · p={pval(t.p_value)}</p>
        </div>
        <table className="num w-full self-start text-[13px]">
          <thead><tr className="text-left text-xs text-ink-3"><th className="pb-1 font-normal">Arm</th><th className="pb-1 text-right font-normal">n</th>
            <th className="pb-1 text-right font-normal">Conversions</th><th className="pb-1 text-right font-normal">Rate</th><th className="pb-1 text-right font-normal">95% CI</th></tr></thead>
          <tbody>
            <tr className="border-t border-line"><td className="py-1">Control</td><td className="text-right">{int(t.baseline_n)}</td><td className="text-right">{int(t.baseline_x)}</td>
              <td className="text-right">{pct(t.baseline_rate, 2)}</td><td className="text-right text-ink-3">{pct(t.baseline_ci[0], 2)}–{pct(t.baseline_ci[1], 2)}</td></tr>
            <tr className="border-t border-line"><td className="py-1">Treatment</td><td className="text-right">{int(t.current_n)}</td><td className="text-right">{int(t.current_x)}</td>
              <td className="text-right font-medium">{pct(t.current_rate, 2)}</td><td className="text-right text-ink-3">{pct(t.current_ci[0], 2)}–{pct(t.current_ci[1], 2)}</td></tr>
          </tbody>
        </table>
      </div>
      <div className="grid gap-3 text-xs md:grid-cols-2">
        <p className="rounded-md bg-sunken p-2.5 text-ink-2"><b className="font-medium text-ink">Sample-ratio check:</b> χ²={r.srm.chi2.toFixed(2)}, p={pval(r.srm.p_value)} —{" "}
          {r.srm.mismatch ? "mismatch: fix assignment/logging before reading results." : `consistent with the expected ${Math.round(r.srm.expected_split * 100)}/${100 - Math.round(r.srm.expected_split * 100)} split.`}</p>
        {r.guardrails.length > 0 && (
          <div className="rounded-md bg-sunken p-2.5">
            <p className="mb-1 font-medium text-ink">Guardrails</p>
            {r.guardrails.map((g) => (
              <p key={g.name} className="num text-ink-2">{g.status === "pass" ? "✓" : g.status === "fail" ? "✕" : "?"} {g.name}: {pct(g.control_rate, 2)} → {pct(g.treatment_rate, 2)} ({pp(g.abs_change_pp, 2)}, CI {g.diff_ci_pp[0].toFixed(2)} to {g.diff_ci_pp[1].toFixed(2)}; margin −{g.max_degradation_pp}pp) — <b>{g.status}</b></p>
            ))}
          </div>
        )}
      </div>
      <p className="text-2xs text-ink-3">Decision rule: {r.decision_rule} {r.p_value_note}</p>
      {r.simulation && <p className="text-2xs text-warn">{r.simulation.note} True rates: control {pct(r.simulation.true_control_rate, 2)}, treatment {pct(r.simulation.true_treatment_rate, 2)} (seed {r.simulation.seed}).</p>}
    </div>
  );
}

interface Historical { experiment_id: string; name: string; population: string; window: string[];
  readouts: Record<string, { statement: string; mde_pp: number }>; analysis: ResultsAnalysis; queries: QueryRecord[] }

export function MeasureView() {
  const { investigation } = useWorkspace();
  const [mode, setMode] = useState<"logged" | "enter" | "simulate">("logged");
  return (
    <div className="space-y-5">
      <Segmented value={mode} onChange={setMode} options={[
        { value: "logged", label: "Logged experiment (EXP-014)" }, { value: "enter", label: "Enter results" }, { value: "simulate", label: "Simulate the planned test" }]} />
      {mode === "logged" && <Logged />}
      {mode === "enter" && <EnterResults />}
      {mode === "simulate" && <Simulate plan={investigation?.recommended_experiment ?? null} />}
    </div>
  );
}

function Logged() {
  const { data, error, loading } = useApi<Historical>("/experiments/historical", undefined);
  if (error) return <ErrorBox error={error} />;
  if (loading || !data) return <Spinner />;
  return (
    <Card title={`${data.experiment_id}: ${data.name}`} subtitle={`${data.population} · ${data.window.join(" to ")}`}
      actions={<Pill tone="good">Randomised — causal evidence</Pill>}>
      <ResultView r={data.analysis} mde={data.readouts.listing_to_contact_start?.mde_pp} />
      <div className="mt-4 space-y-1 border-t border-line pt-3 text-xs text-ink-2">
        {Object.entries(data.readouts).map(([k, v]) => <p key={k}>• {v.statement}</p>)}
      </div>
      <p className="mt-3 text-xs text-ink-3">Lesson: this test was sized too small for the effect it was looking for. A “not significant” result here is weak evidence of no effect — the plan above would size it properly.</p>
      <QueryPanel queries={data.queries} />
    </Card>
  );
}

function EnterResults() {
  const [v, setV] = useState({ cn: 12000, cx: 1020, tn: 12040, tx: 1130, gcn: 12000, gcx: 690, gtn: 12040, gtx: 712, mde: 0.5, label: "Listing → contact" });
  const [res, setRes] = useState<ResultsAnalysis | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const run = async () => {
    setErr(null);
    try {
      setRes(await api<ResultsAnalysis>("/experiments/analyze", {
        metric_label: v.label, control: { n: v.cn, conversions: v.cx }, treatment: { n: v.tn, conversions: v.tx }, mde_pp: v.mde || null,
        guardrails: [{ name: "Qualified-lead rate", control: { n: v.gcn, conversions: v.gcx }, treatment: { n: v.gtn, conversions: v.gtx }, max_degradation_pp: 1 }],
      }));
    } catch (e) { setErr((e as Error).message); setRes(null); }
  };
  const f = (k: keyof typeof v, label: string) => (
    <Field label={label}><input className={inputCls} type="number" value={v[k] as number} onChange={(e) => setV({ ...v, [k]: Number(e.target.value) })} /></Field>
  );
  return (
    <Card title="Analyse supplied results" subtitle="Enter observed counts from your experimentation platform. The analysis is labelled as an observed outcome.">
      <div className="grid gap-3 sm:grid-cols-4">
        {f("cn", "Control sessions")}{f("cx", "Control conversions")}{f("tn", "Treatment sessions")}{f("tx", "Treatment conversions")}
        {f("gcn", "Guardrail: control n")}{f("gcx", "Guardrail: control qualified")}{f("gtn", "Guardrail: treatment n")}{f("gtx", "Guardrail: treatment qualified")}
        {f("mde", "MDE worth shipping (pp)")}
        <div className="flex items-end"><Button variant="primary" onClick={run}>Analyse</Button></div>
      </div>
      {err && <div className="mt-3"><ErrorBox error={err} /></div>}
      {res && <div className="mt-5 border-t border-line pt-4"><ResultView r={res} mde={v.mde} /></div>}
    </Card>
  );
}

function Simulate({ plan }: { plan: { baseline_rate: number; sample_size_per_arm: number | null; mde: { absolute_pp: number; relative: number } | null; primary_metric: { label: string }; name: string } | null }) {
  const [v, setV] = useState({ baseline: 0.087, lift: 0.1, n: 20000, seed: 7, mde: 0.87, label: "Listing → contact" });
  useEffect(() => {
    if (plan?.sample_size_per_arm && plan.mde) {
      setV((s) => ({ ...s, baseline: Number(plan.baseline_rate.toFixed(4)), n: plan.sample_size_per_arm!, lift: Number(plan.mde!.relative.toFixed(3)),
        mde: Number(plan.mde!.absolute_pp.toFixed(2)), label: plan.primary_metric.label }));
    }
  }, [plan]);
  const [res, setRes] = useState<ResultsAnalysis | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setBusy(true); setErr(null);
    try {
      setRes(await api<ResultsAnalysis>("/experiments/simulate", {
        baseline_rate: v.baseline, true_relative_lift: v.lift, n_per_arm: v.n, seed: v.seed, mde_pp: v.mde, metric_label: v.label,
        guardrail_rate: 0.7, guardrail_true_change_pp: 0,
      }));
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  const f = (k: keyof typeof v, label: string, step = "any", hint?: string) => (
    <Field label={label} hint={hint}><input className={inputCls} type="number" step={step} value={v[k] as number} onChange={(e) => setV({ ...v, [k]: Number(e.target.value) })} /></Field>
  );
  return (
    <Card title="Simulate the planned experiment" subtitle={plan?.sample_size_per_arm ? `Pre-filled from “${plan.name}”. Change the true lift to see how often the decision rule gets it right.` : "No sized plan in the active case — using example inputs."}
      actions={<Pill tone="warn">Simulated outcome</Pill>}>
      <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
        {f("baseline", "Control rate", "0.001")}{f("lift", "True relative lift", "0.01", "Unknown in reality")}{f("n", "n per arm", "100")}
        {f("mde", "MDE (pp)", "0.01")}{f("seed", "Random seed", "1")}
        <div className="flex items-end gap-2"><Button variant="primary" onClick={run} disabled={busy}>{busy ? "Simulating…" : "Simulate"}</Button>
          <Button onClick={() => { setV({ ...v, seed: v.seed + 1 }); }}>New seed</Button></div>
      </div>
      {err && <div className="mt-3"><ErrorBox error={err} /></div>}
      {res && <div className="mt-5 border-t border-line pt-4"><ResultView r={res} mde={v.mde} /></div>}
    </Card>
  );
}
