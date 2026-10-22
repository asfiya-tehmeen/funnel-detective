"use client";

import { useEffect, useState } from "react";
import { Button, Card, EmptyState, ErrorBox, Field, InfoTip, Pill, QueryPanel, Spinner, inputCls } from "@/components/ui";
import { useWorkspace } from "@/components/workspace-context";
import { api } from "@/lib/api";
import { CATEGORY_LABEL, filtersLabel, int, pct } from "@/lib/format";
import type { ExperimentPlan, ImpactResult, QueryRecord } from "@/lib/types";

const CATEGORIES = ["technical_regression", "behavioral_change", "traffic_composition", "selection_effect", "instrumentation_issue", "random_variation"];

export function DecideView() {
  const { investigation, filters, comparison, setStage } = useWorkspace();
  const [plan, setPlan] = useState<ExperimentPlan | null>(investigation?.recommended_experiment ?? null);
  const [category, setCategory] = useState(investigation?.recommended_experiment?.hypothesis_category ?? "behavioral_change");
  const [alpha, setAlpha] = useState(0.05);
  const [power, setPower] = useState(0.8);
  const [alloc, setAlloc] = useState(1);
  const [autoMde, setAutoMde] = useState(true);
  const [relMde, setRelMde] = useState(0.1);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setPlan(investigation?.recommended_experiment ?? null);
    if (investigation?.recommended_experiment) setCategory(investigation.recommended_experiment.hypothesis_category);
  }, [investigation]);

  const replan = async () => {
    setBusy(true); setErr(null);
    try {
      const target = plan?.target_segment ?? filters;
      setPlan(await api<ExperimentPlan>("/experiments/plan", {
        category, filters: target, comparison, alpha, power, traffic_allocation: alloc, auto_mde: autoMde, relative_mde: relMde,
      }));
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };

  return (
    <div className="space-y-5">
      <Card title="Experiment planner" subtitle={investigation ? `Recommended from the leading explanation of “${investigation.question}”` : "Pick an explanation to test, or run an investigation first for a recommendation"}
        actions={<Pill tone="warn">Forecast — not an observed outcome</Pill>}>
        <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
          <Field label="Explanation to act on">
            <select className={inputCls} value={category} onChange={(e) => setCategory(e.target.value)}>
              {CATEGORIES.map((c) => <option key={c} value={c}>{CATEGORY_LABEL[c]}</option>)}
            </select>
          </Field>
          <Field label="α (two-sided)"><input className={inputCls} type="number" step="0.01" min="0.001" max="0.2" value={alpha} onChange={(e) => setAlpha(Number(e.target.value))} /></Field>
          <Field label="Power"><input className={inputCls} type="number" step="0.05" min="0.5" max="0.99" value={power} onChange={(e) => setPower(Number(e.target.value))} /></Field>
          <Field label="Traffic in test"><input className={inputCls} type="number" step="0.1" min="0.1" max="1" value={alloc} onChange={(e) => setAlloc(Number(e.target.value))} /></Field>
          <Field label="MDE" hint={autoMde ? "Smallest that fits ≤6 weeks" : "Relative lift"}>
            <div className="flex gap-1">
              <select className={inputCls} value={autoMde ? "auto" : "fixed"} onChange={(e) => setAutoMde(e.target.value === "auto")}>
                <option value="auto">Auto</option><option value="fixed">Fixed</option></select>
              {!autoMde && <input className={inputCls} type="number" step="0.01" min="0.01" max="1" value={relMde} onChange={(e) => setRelMde(Number(e.target.value))} />}
            </div>
          </Field>
          <div className="flex items-end"><Button variant="primary" onClick={replan} disabled={busy}>{busy ? "Sizing…" : plan ? "Recalculate" : "Build plan"}</Button></div>
        </div>
        <p className="mt-2 text-2xs text-ink-3">Target segment: {filtersLabel(plan?.target_segment ?? filters)}. Baseline rate and traffic come from the current window of the data.</p>
        {err && <div className="mt-3"><ErrorBox error={err} /></div>}
        {!plan && !busy && <div className="mt-4"><EmptyState title="No plan yet">Build a plan for the selected explanation and scope.</EmptyState></div>}
        {busy && <div className="mt-4"><Spinner /></div>}
        {plan && <PlanView plan={plan} />}
      </Card>
      {plan && <ImpactCalculator plan={plan} />}
      {plan?.kind === "experiment" && (
        <div className="flex justify-end"><Button variant="ghost" onClick={() => setStage("measure")}>Read out results → Measure</Button></div>
      )}
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid gap-1 border-t border-line py-2.5 sm:grid-cols-[180px_1fr]">
      <dt className="text-xs font-medium text-ink-3">{label}</dt>
      <dd className="text-[13px] text-ink">{children}</dd>
    </div>
  );
}

function PlanView({ plan }: { plan: ExperimentPlan }) {
  return (
    <div className="mt-5">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <h3 className="text-[15px] font-semibold">{plan.name}</h3>
        <Pill tone={plan.kind === "experiment" ? "accent" : "neutral"}>{plan.kind === "experiment" ? "A/B test" : plan.kind === "data_fix" ? "Data fix — no A/B test" : "Monitor — no A/B test"}</Pill>
      </div>
      <p className="mb-3 text-xs text-ink-3">{plan.forecast_note}</p>
      <dl>
        <Row label="Problem statement">{plan.problem_statement}</Row>
        <Row label="Target segment">{filtersLabel(plan.target_segment)}</Row>
        <Row label="Hypothesis">{plan.hypothesis}</Row>
        <Row label="Proposed intervention">{plan.intervention}</Row>
        <Row label="Control / treatment"><span className="text-ink-2">Control:</span> {plan.control}<br /><span className="text-ink-2">Treatment:</span> {plan.treatment}</Row>
        <Row label="Primary metric">{plan.primary_metric.label} <span className="text-ink-3">— {plan.primary_metric.numerator} ÷ {plan.primary_metric.denominator}</span>
          <span className="num ml-2 text-ink-3">current {pct(plan.baseline_rate, 2)} (pre-change {pct(plan.pre_change_rate, 2)})</span></Row>
        <Row label="Guardrail metrics">{plan.guardrail_metrics.length ? plan.guardrail_metrics.map((g) => g.label).join(" · ") : "—"}</Row>
        <Row label="Expected mechanism">{plan.mechanism}</Row>
        {plan.mde && (
          <Row label="MDE & sample size">
            <span className="num">MDE {plan.mde.absolute_pp.toFixed(2)}pp ({(plan.mde.relative * 100).toFixed(0)}% relative) → <b>{int(plan.sample_size_per_arm)}</b> per arm</span>
            <span className="block text-xs text-ink-3">{plan.mde.basis}</span>
          </Row>
        )}
        {plan.duration_days && (
          <Row label="Duration"><span className="num font-medium">{plan.duration_days} days</span> <span className="block text-xs text-ink-3">{plan.duration_note}</span></Row>
        )}
        {plan.monitoring_sample_needed && (
          <Row label="Data needed"><span className="num">{int(plan.monitoring_sample_needed.sessions_per_window)} sessions per window (~{plan.monitoring_sample_needed.days_at_current_traffic} days) to detect a {(plan.monitoring_sample_needed.relative_change * 100).toFixed(0)}% change</span></Row>
        )}
        {plan.assumptions && <Row label="Assumptions"><ul className="list-disc pl-4 text-ink-2">{plan.assumptions.map((a) => <li key={a}>{a}</li>)}</ul></Row>}
        <Row label="Implementation cost">{plan.implementation_cost}</Row>
        <Row label="Risks & failure conditions"><ul className="list-disc pl-4 text-ink-2">{plan.risks.map((r) => <li key={r}>{r}</li>)}</ul></Row>
        <Row label="Decision rule"><span className="text-ink-2">{plan.decision_rule}</span></Row>
      </dl>
      <QueryPanel queries={plan.queries} />
    </div>
  );
}

interface Defaults { eligible_sessions_per_month: number; baseline_rate: number; qualified_rate: number; viewing_rate: number; source: string; queries: QueryRecord[] }

function ImpactCalculator({ plan }: { plan: ExperimentPlan }) {
  const { comparison } = useWorkspace();
  const [inputs, setInputs] = useState<Record<string, number | string | null>>({});
  const [defaults, setDefaults] = useState<Defaults | null>(null);
  const [result, setResult] = useState<ImpactResult | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api<Defaults>("/impact/defaults", { metric: "listing_to_contact", filters: plan.target_segment, comparison }).then((d) => {
      if (!alive) return;
      setDefaults(d);
      // Default uplift scenarios: half / full / 1.5× the planned MDE, expressed on listing→contact.
      const mdePp = plan.mde ? plan.mde.relative * d.baseline_rate * 100 : 0.5;
      setInputs({
        eligible_sessions_per_month: d.eligible_sessions_per_month, baseline_rate: Number(d.baseline_rate.toFixed(4)),
        uplift_pp_low: Number((mdePp * 0.5).toFixed(2)), uplift_pp_base: Number(mdePp.toFixed(2)), uplift_pp_high: Number((mdePp * 1.5).toFixed(2)),
        qualified_rate: Number(d.qualified_rate.toFixed(3)), viewing_rate: Number(d.viewing_rate.toFixed(3)),
        value_per_qualified_contact: null, value_currency: "AED", value_source: "", months: 12,
      });
    }).catch((e) => alive && setErr((e as Error).message));
    return () => { alive = false; };
  }, [plan, comparison]);

  useEffect(() => {
    if (!inputs.eligible_sessions_per_month) return;
    const body = { ...inputs, value_per_qualified_contact: inputs.value_per_qualified_contact === null || inputs.value_per_qualified_contact === "" ? null : Number(inputs.value_per_qualified_contact),
      value_source: inputs.value_source || null };
    const t = setTimeout(() => {
      api<ImpactResult>("/impact", body).then((r) => { setResult(r); setErr(null); }).catch((e) => setErr((e as Error).message));
    }, 250);
    return () => clearTimeout(t);
  }, [inputs]);

  const num = (k: string, label: string, step = "0.01", hint?: string) => (
    <Field label={label} hint={hint}>
      <input className={inputCls} type="number" step={step} value={(inputs[k] as number | null) ?? ""}
        onChange={(e) => setInputs({ ...inputs, [k]: e.target.value === "" ? null : Number(e.target.value) })} />
    </Field>
  );
  return (
    <Card title="Business impact (modelled)" subtitle="Additional qualified contacts = eligible sessions × absolute uplift × qualified-lead rate. Every input is editable."
      actions={<Pill tone="warn">Modelled estimate — not realised revenue</Pill>}>
      {!defaults && !err && <Spinner />}
      {err && <ErrorBox error={err} />}
      {defaults && (
        <>
          <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-5">
            {num("eligible_sessions_per_month", "Eligible sessions / month", "100")}
            {num("baseline_rate", "Baseline listing → contact", "0.001")}
            {num("uplift_pp_low", "Uplift low (pp)")}
            {num("uplift_pp_base", "Uplift base (pp)")}
            {num("uplift_pp_high", "Uplift high (pp)")}
            {num("qualified_rate", "Qualified-lead rate", "0.01")}
            {num("viewing_rate", "Viewing rate per contact", "0.01")}
            {num("months", "Horizon (months)", "1")}
            <Field label="Value per qualified contact" hint="Optional — leave blank for no money figure">
              <input className={inputCls} type="number" step="10" placeholder="e.g. from finance" value={(inputs.value_per_qualified_contact as number | null) ?? ""}
                onChange={(e) => setInputs({ ...inputs, value_per_qualified_contact: e.target.value === "" ? null : Number(e.target.value) })} />
            </Field>
            <Field label="Source of that value">
              <input className={inputCls} type="text" placeholder="Who provided it, and how" value={(inputs.value_source as string) ?? ""}
                onChange={(e) => setInputs({ ...inputs, value_source: e.target.value })} />
            </Field>
          </div>
          <p className="mt-2 text-2xs text-ink-3">Defaults: {defaults.source} Uplift defaults are ½×, 1× and 1.5× the plan&apos;s MDE translated to listing → contact — forecasts, not results.</p>
          {result && (
            <div className="mt-4 overflow-x-auto">
              <table className="num w-full min-w-[560px] text-[13px]">
                <thead><tr className="text-left text-xs text-ink-3"><th className="pb-2 font-normal">Scenario</th><th className="pb-2 text-right font-normal">Uplift</th>
                  <th className="pb-2 text-right font-normal">+Contacts / mo</th><th className="pb-2 text-right font-normal">+Qualified / mo</th>
                  <th className="pb-2 text-right font-normal">+Viewings / mo</th><th className="pb-2 text-right font-normal">+Qualified total</th>
                  {result.scenarios.base.modelled_value_total !== undefined && <th className="pb-2 text-right font-normal">Modelled value total</th>}</tr></thead>
                <tbody>
                  {(["low", "base", "high"] as const).map((k) => {
                    const s = result.scenarios[k];
                    return (
                      <tr key={k} className="border-t border-line">
                        <td className="py-1.5 font-medium capitalize">{k}</td>
                        <td className="text-right">{s.uplift_pp > 0 ? "+" : ""}{s.uplift_pp.toFixed(2)}pp</td>
                        <td className="text-right">{int(s.additional_contacts_per_month)}</td>
                        <td className="text-right font-medium">{int(s.additional_qualified_per_month)}</td>
                        <td className="text-right">{int(s.additional_viewings_per_month)}</td>
                        <td className="text-right">{int(s.additional_qualified_total)}</td>
                        {s.modelled_value_total !== undefined && <td className="text-right">{inputs.value_currency} {int(s.modelled_value_total)}</td>}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              <p className="mt-2 text-xs text-ink-3">{result.disclaimer} {result.value_note}</p>
              <p className="mt-1 text-2xs text-ink-3">Formulas: {result.formulas.join(" · ")} <InfoTip text="Rounding: displayed values are rounded to whole contacts; the model itself is continuous." /></p>
            </div>
          )}
          <QueryPanel queries={defaults.queries} />
        </>
      )}
    </Card>
  );
}
