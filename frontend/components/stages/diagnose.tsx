"use client";

import { useState } from "react";
import { ChangeBars, ShareBars } from "@/components/charts";
import { Card, Delta, ErrorBox, InfoTip, QueryPanel, Segmented, Spinner } from "@/components/ui";
import { useApi, useWorkspace } from "@/components/workspace-context";
import { filtersLabel, humanize, int, pct, pp, pval, segLabel } from "@/lib/format";
import type { Decomposition, Dimension, PropTest, QueryRecord, SegmentTable, SimpsonData } from "@/lib/types";

const DIMS: { value: Dimension; label: string }[] = [
  { value: "channel", label: "Channel" }, { value: "device", label: "Device" }, { value: "market", label: "Market" },
  { value: "price_band", label: "Price band" }, { value: "property_type", label: "Property" },
  { value: "visitor_type", label: "Visitor" }, { value: "intent", label: "Intent" },
];
const METRICS = [
  { value: "listing_to_contact", label: "Listing → contact" },
  { value: "listing_to_contact_start", label: "→ contact start" },
  { value: "contact_start_to_submit", label: "Start → submit" },
  { value: "contact_to_viewing", label: "Contact → viewing" },
  { value: "search_to_listing", label: "Search → listing" },
];
const STEP_LABEL: Record<string, string> = {
  search_to_listing: "Search → listing", listing_to_contact_start: "Listing → contact start",
  contact_start_to_submit: "Contact start → submit (form completion)", contact_to_viewing: "Contact → viewing",
  save_rate: "Listing → save",
};

interface BreakdownResp { table: SegmentTable; decomposition: Decomposition; steps: Record<string, PropTest>; queries: QueryRecord[] }

export function DiagnoseView() {
  const { filters, comparison, version, investigation, setStage } = useWorkspace();
  const [metric, setMetric] = useState(investigation?.finding?.metric && METRICS.some((m) => m.value === investigation.finding!.metric)
    ? investigation.finding.metric : "listing_to_contact");
  const [dim, setDim] = useState<Dimension>("channel");
  const b = useApi<BreakdownResp>("/breakdown", { filters, comparison, metric, dimension: dim, adjust_for: "channel" }, [version]);
  const s = useApi<SimpsonData>("/simpson", { filters, comparison, metric, dimension: "channel" }, [version]);

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-3">
        <p className="text-[13px] text-ink-2">Breaking down <b className="font-medium text-ink">{filtersLabel(filters)}</b></p>
        <Segmented size="sm" value={metric} options={METRICS} onChange={setMetric} />
      </div>
      {b.error && <ErrorBox error={b.error} onRetry={b.reload} />}
      {!b.data && !b.error && <Spinner label="Breaking down the change…" />}
      {b.data && (
        <>
          <Card title="Where in the journey did it change?" subtitle="Each step's conversion, baseline vs current. Form-completion drops usually point to product/technical problems; contact-start drops to intent.">
            <StepsTable steps={b.data.steps} />
          </Card>
          <div className="grid gap-5 lg:grid-cols-2">
            <Card title={`Change by ${humanize(dim).toLowerCase()}`}
              subtitle="Bars: raw change with 95% CI. Table adds the change holding channel mix fixed and BH q-values."
              actions={<select aria-label="Dimension" value={dim} onChange={(e) => setDim(e.target.value as Dimension)}
                className="rounded border border-line bg-raised px-2 py-1 text-xs">{DIMS.map((d) => <option key={d.value} value={d.value}>{d.label}</option>)}</select>}>
              <ChangeBars data={b.data.table.rows.filter((r) => r.tested).map((r) => ({
                label: segLabel(r.segment), change: r.abs_change_pp, lo: r.diff_ci_pp?.[0], hi: r.diff_ci_pp?.[1], n: r.current_n }))} />
              <SegmentRows table={b.data.table} />
            </Card>
            <Card title={`Mix vs rate: ${humanize(dim).toLowerCase()}`}
              subtitle="Kitagawa decomposition. Mix = change from segment shares shifting; rate = change within segments. They sum to the total exactly.">
              <DecompositionView d={b.data.decomposition} />
            </Card>
          </div>
          <QueryPanel queries={b.data.queries} />
        </>
      )}
      <Card title="Simpson's paradox check (by channel)" subtitle="Does the aggregate tell the same story as the segments it is made of?">
        {s.error && <ErrorBox error={s.error} onRetry={s.reload} />}
        {!s.data && !s.error && <Spinner />}
        {s.data && <SimpsonView s={s.data} />}
      </Card>
      <div className="flex justify-end"><button className="text-[13px] text-accent hover:underline" onClick={() => setStage("challenge")}>Test explanations → Challenge</button></div>
    </div>
  );
}

function StepsTable({ steps }: { steps: Record<string, PropTest> }) {
  return (
    <div className="overflow-x-auto">
      <table className="num w-full min-w-[620px] text-[13px]">
        <thead><tr className="text-left text-xs text-ink-3">
          <th className="pb-2 font-normal">Step</th><th className="pb-2 text-right font-normal">Baseline</th>
          <th className="pb-2 text-right font-normal">Current</th><th className="pb-2 text-right font-normal">Change</th>
          <th className="pb-2 text-right font-normal">Relative</th><th className="pb-2 text-right font-normal">95% CI (pp)</th>
          <th className="pb-2 text-right font-normal">p</th><th className="pb-2 text-right font-normal">Denominator (cur.)</th>
        </tr></thead>
        <tbody>
          {Object.entries(steps).map(([k, t]) => (
            <tr key={k} className="border-t border-line">
              <td className="py-1.5">{STEP_LABEL[k] ?? k}</td>
              <td className="text-right text-ink-2">{pct(t.baseline_rate)}</td>
              <td className="text-right font-medium">{pct(t.current_rate)}</td>
              <td className="text-right"><Delta value={t.abs_change_pp} significant={t.significant} /></td>
              <td className="text-right text-ink-2">{t.rel_change_pct === null ? "—" : `${t.rel_change_pct > 0 ? "+" : ""}${t.rel_change_pct.toFixed(0)}%`}</td>
              <td className="text-right text-ink-3">{t.diff_ci_pp[0].toFixed(2)} to {t.diff_ci_pp[1].toFixed(2)}</td>
              <td className="text-right text-ink-3">{pval(t.p_value)}</td>
              <td className="text-right text-ink-3">{int(t.current_n)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SegmentRows({ table }: { table: SegmentTable }) {
  return (
    <div className="mt-3 overflow-x-auto">
      <table className="num w-full min-w-[480px] text-xs">
        <thead><tr className="text-left text-ink-3">
          <th className="pb-1 font-normal">Segment</th><th className="pb-1 text-right font-normal">Base</th>
          <th className="pb-1 text-right font-normal">Cur</th><th className="pb-1 text-right font-normal">Raw Δ</th>
          <th className="pb-1 text-right font-normal">
            <InfoTip text="Change after standardising over acquisition channel: weights each channel equally in both windows, so a channel-mix shift cannot move it.">Within-ch. Δ</InfoTip></th>
          <th className="pb-1 text-right font-normal">q</th><th className="pb-1 text-right font-normal">n (cur)</th>
        </tr></thead>
        <tbody>
          {table.rows.map((r) => (
            <tr key={JSON.stringify(r.segment)} className="border-t border-line">
              <td className="py-1">{segLabel(r.segment)}</td>
              <td className="text-right text-ink-2">{pct(r.baseline_rate)}</td>
              <td className="text-right">{pct(r.current_rate)}</td>
              <td className="text-right">{r.tested ? <Delta value={r.abs_change_pp} /> : <span className="text-ink-3">too small</span>}</td>
              <td className="text-right">{r.adj_change_pp !== undefined ? <Delta value={r.adj_change_pp} /> : <span className="text-ink-3">—</span>}</td>
              <td className="text-right text-ink-3">{pval(r.adj_q_value ?? r.q_value)}</td>
              <td className="text-right text-ink-3">{int(r.current_n)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {table.multiple_comparison_note && <p className="mt-2 text-2xs text-ink-3">{table.multiple_comparison_note}</p>}
    </div>
  );
}

function DecompositionView({ d }: { d: Decomposition }) {
  const share = d.mix_share_of_change;
  return (
    <div>
      <div className="grid grid-cols-3 gap-2 text-center">
        {[["Total change", d.total_change_pp], ["Mix effect", d.mix_effect_pp], ["Rate effect", d.rate_effect_pp]].map(([l, v]) => (
          <div key={l as string} className="rounded-md bg-sunken p-2.5">
            <p className="text-2xs text-ink-3">{l}</p>
            <p className="num text-[17px] font-semibold">{pp(v as number, 2)}</p>
          </div>
        ))}
      </div>
      {share !== null && (
        <p className="mt-2 text-xs text-ink-2">
          Mix accounts for <b className="num font-semibold">{Math.round(share * 100)}%</b> of the change in this breakdown.
          {Math.abs(share) >= 0.5 ? " Most of the movement is about who arrived, not what they did." : " Most of the movement is within segments."}
        </p>
      )}
      <p className="mb-1 mt-3 text-xs font-medium text-ink-2">Share of denominator, baseline vs current</p>
      <ShareBars data={d.segments.map((s) => ({ label: segLabel(s.segment), baseline: s.baseline_share, current: s.current_share }))} />
      <table className="num mt-2 w-full text-xs">
        <thead><tr className="text-left text-ink-3"><th className="font-normal">Segment</th><th className="text-right font-normal">Rate base → cur</th>
          <th className="text-right font-normal">Mix pp</th><th className="text-right font-normal">Rate pp</th></tr></thead>
        <tbody>
          {d.segments.map((s) => (
            <tr key={JSON.stringify(s.segment)} className="border-t border-line">
              <td className="py-1">{segLabel(s.segment)}</td>
              <td className="text-right text-ink-2">{pct(s.baseline_rate)} → {pct(s.current_rate)}</td>
              <td className="text-right">{pp(s.mix_effect_pp, 2)}</td><td className="text-right">{pp(s.rate_effect_pp, 2)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function SimpsonView({ s }: { s: SimpsonData }) {
  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_1.4fr]">
      <div className="space-y-3">
        <div className="rounded-md bg-sunken p-3">
          <p className="text-2xs text-ink-3">Aggregate</p>
          <p className="num text-[15px] font-semibold">{pct(s.total.baseline_rate)} → {pct(s.total.current_rate)} <Delta value={s.total.abs_change_pp} digits={2} /></p>
        </div>
        <p className="text-xs leading-relaxed text-ink-2">{s.explanation}</p>
        {s.decomposition && (
          <p className="text-xs text-ink-2">Kitagawa: mix <b className="num">{pp(s.decomposition.mix_effect_pp, 2)}</b> + within-segment <b className="num">{pp(s.decomposition.rate_effect_pp, 2)}</b> = <b className="num">{pp(s.decomposition.total_change_pp, 2)}</b>.</p>
        )}
      </div>
      <table className="num w-full text-xs">
        <thead><tr className="text-left text-ink-3"><th className="pb-1 font-normal">Channel</th><th className="pb-1 text-right font-normal">Share base → cur</th>
          <th className="pb-1 text-right font-normal">Rate base → cur</th><th className="pb-1 text-right font-normal">Δ</th><th className="pb-1 text-right font-normal">q</th></tr></thead>
        <tbody>
          {s.rows.map((r) => (
            <tr key={r.segment} className="border-t border-line">
              <td className="py-1">{r.segment}</td>
              <td className="text-right text-ink-2">{pct(r.baseline_share, 0)} → {pct(r.current_share, 0)}</td>
              <td className="text-right">{pct(r.baseline_rate)} → {pct(r.current_rate)}</td>
              <td className="text-right"><Delta value={r.abs_change_pp} significant={(r.q_value ?? 1) < 0.05} /></td>
              <td className="text-right text-ink-3">{pval(r.q_value)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <QueryPanel queries={s.queries} />
    </div>
  );
}
