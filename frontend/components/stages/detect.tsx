"use client";

import { useState } from "react";
import { FunnelChart, TrendChart } from "@/components/charts";
import { Button, Card, Delta, EmptyState, ErrorBox, InfoTip, Pill, QueryPanel, Segmented, Spinner } from "@/components/ui";
import { useApi, useWorkspace } from "@/components/workspace-context";
import { api } from "@/lib/api";
import { int, pct, pp, pval, segLabel, shortDate } from "@/lib/format";
import type { Kpi, Overview, QueryRecord, Scan, SegmentRow, Warning } from "@/lib/types";

export function Legend({ comparison }: { comparison: { baseline: { start: string; end: string }; current: { start: string; end: string } } }) {
  return (
    <div className="flex flex-wrap gap-3 text-2xs text-ink-3">
      <span className="inline-flex items-center gap-1"><span className="h-2 w-3 rounded-sm" style={{ background: "var(--chart-baseline)" }} />
        Baseline {shortDate(comparison.baseline.start)}–{shortDate(comparison.baseline.end)}</span>
      <span className="inline-flex items-center gap-1"><span className="h-2 w-3 rounded-sm" style={{ background: "var(--chart-current)" }} />
        Current {shortDate(comparison.current.start)}–{shortDate(comparison.current.end)}</span>
    </div>
  );
}

function KpiTile({ k }: { k: Kpi }) {
  if (k.error || !k.test) {
    return <Card dense title={k.label}><p className="text-xs text-ink-3">{k.error ?? "No data"}</p></Card>;
  }
  const t = k.test;
  const adj = k.channel_adjusted;
  return (
    <div className="rounded-lg border border-line bg-raised p-4">
      <div className="flex items-center justify-between">
        <p className="text-xs font-medium text-ink-2">{k.label}</p>
        <InfoTip text={`${k.description} Numerator: ${k.numerator}. Denominator: ${k.denominator}. Rates are per session; 95% Wilson intervals; ${t.test}.`} />
      </div>
      <p className="mt-1.5 text-[26px] font-semibold leading-none tracking-tight text-ink">{pct(t.current_rate)}</p>
      <div className="mt-2 flex flex-wrap items-center gap-x-2 text-xs">
        <Delta value={t.abs_change_pp} significant={t.significant} />
        <span className="text-ink-3">vs {pct(t.baseline_rate)} · p={pval(t.p_value)}</span>
      </div>
      {adj && (
        <p className="mt-1 text-2xs text-ink-3">
          Holding channel mix fixed: <span className="num font-medium text-ink-2">{pp(adj.adj_change_pp)}</span> (p={pval(adj.adj_p_value)})
        </p>
      )}
      <p className="num mt-2 border-t border-line pt-2 text-2xs text-ink-3">
        {int(t.current_x)} / {int(t.current_n)} sessions · CI {pct(t.current_ci[0])}–{pct(t.current_ci[1])}
      </p>
    </div>
  );
}

const TREND_OPTIONS = [
  { value: "listing_to_contact", label: "Listing → contact" },
  { value: "search_to_listing", label: "Search → listing" },
  { value: "contact_to_viewing", label: "Contact → viewing" },
];

export function DetectView() {
  const { filters, comparison, version, runInvestigation, setFilters, setStage } = useWorkspace();
  const { data, error, loading, reload } = useApi<Overview>("/overview", { filters, comparison }, [version]);
  const [trendMetric, setTrendMetric] = useState("listing_to_contact");

  if (error) return <ErrorBox error={error} onRetry={reload} />;
  if (!data) return <div className="py-16"><Spinner label="Computing funnel metrics…" /></div>;

  const queryById = (id?: string): QueryRecord[] => data.queries.filter((q) => q.id === id);
  const qc = data.qualified_contacts;
  return (
    <div className={loading ? "space-y-5 opacity-70 transition-opacity" : "space-y-5"}>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {data.kpis.map((k) => <KpiTile key={k.metric} k={k} />)}
        <div className="rounded-lg border border-line bg-raised p-4">
          <div className="flex items-center justify-between">
            <p className="text-xs font-medium text-ink-2">Qualified contacts</p>
            <InfoTip text={qc.definition} />
          </div>
          <p className="mt-1.5 text-[26px] font-semibold leading-none tracking-tight">{int(qc.current)}</p>
          <p className="mt-2 text-xs text-ink-3">
            baseline {int(qc.baseline)} · <span className="num">{qc.baseline ? `${(((qc.current - qc.baseline) / qc.baseline) * 100).toFixed(0)}%` : "—"}</span> change
          </p>
          <p className="mt-2 border-t border-line pt-2 text-2xs text-ink-3">A count, not a rate: rises with traffic even when conversion falls.</p>
        </div>
      </div>

      <div className="grid gap-5 lg:grid-cols-5">
        <Card className="lg:col-span-3" title="Conversion trend (weekly)" subtitle="Shaded: baseline (gray) and current (blue) windows · band = 95% interval"
          actions={<Segmented size="sm" value={trendMetric} options={TREND_OPTIONS} onChange={setTrendMetric} />}>
          <TrendChart points={data.trends[trendMetric].points} comparison={data.comparison} />
          <QueryPanel queries={queryById(data.trends[trendMetric].query_id)} />
        </Card>
        <Card className="lg:col-span-2" title="Funnel by step" subtitle="Sessions reaching each step" actions={<Legend comparison={data.comparison} />}>
          <FunnelChart baseline={data.funnel.baseline.steps} current={data.funnel.current.steps} />
          <table className="num mt-2 w-full text-xs">
            <thead><tr className="text-left text-ink-3"><th className="font-normal">Step conversion</th><th className="text-right font-normal">Baseline</th><th className="text-right font-normal">Current</th></tr></thead>
            <tbody>
              {data.funnel.current.steps.slice(1).map((s, i) => (
                <tr key={s.key} className="border-t border-line"><td className="py-1 text-ink-2">{s.label}</td>
                  <td className="text-right">{pct(data.funnel.baseline.steps[i + 1].step_conversion)}</td>
                  <td className="text-right font-medium">{pct(s.step_conversion)}</td></tr>
              ))}
            </tbody>
          </table>
          <p className="mt-2 text-2xs text-ink-3">{data.funnel.current.note}</p>
        </Card>
      </div>

      <div className="grid gap-5 lg:grid-cols-5">
        <Card className="lg:col-span-3" title="Markets: listing → contact" subtitle="Raw change and change holding channel mix fixed (stratified). q = Benjamini–Hochberg across markets.">
          <MarketTable rows={data.markets.rows} onInvestigate={(m) => {
            setFilters({ ...filters, market: [m] });
            runInvestigation(`Why did listing to contact conversion change in ${m}?`, { metric: "listing_to_contact", filters: { ...filters, market: [m] } });
          }} />
          <QueryPanel queries={queryById(data.markets.query_id)} />
        </Card>
        <Card className="lg:col-span-2" title="Data-quality warnings" subtitle="Checked automatically before any conclusion is drawn">
          <Warnings warnings={data.warnings} onInvestigate={(w) => {
            const metric = w.kind === "tracking" ? "listing_to_contact_raw" : "contact_start_to_submit";
            runInvestigation(`Are users abandoning the contact flow, or is tracking broken in ${segLabel(w.segment)}?`, { metric, filters: w.segment as never });
          }} />
        </Card>
      </div>

      <ScanCard onInvestigate={(r, metric) => runInvestigation(`Why did ${metric.replace(/_/g, " ")} change for ${segLabel(r.segment)}?`,
        { metric, filters: { ...filters, ...Object.fromEntries(Object.entries(r.segment).map(([k, v]) => [k, [v]])) } })} />
      <div className="flex justify-end"><Button variant="ghost" onClick={() => setStage("diagnose")}>Break it down → Diagnose</Button></div>
    </div>
  );
}

function MarketTable({ rows, onInvestigate }: { rows: SegmentRow[]; onInvestigate: (m: string) => void }) {
  const sorted = [...rows].sort((a, b) => (a.adj_impact_sessions ?? 0) - (b.adj_impact_sessions ?? 0));
  return (
    <div className="overflow-x-auto">
      <table className="num w-full min-w-[560px] text-[13px]">
        <thead>
          <tr className="text-left text-xs text-ink-3">
            <th className="pb-2 font-normal">Market</th><th className="pb-2 text-right font-normal">Baseline</th>
            <th className="pb-2 text-right font-normal">Current</th><th className="pb-2 text-right font-normal">Raw Δ</th>
            <th className="pb-2 text-right font-normal">Within-channel Δ</th><th className="pb-2 text-right font-normal">q</th>
            <th className="pb-2 text-right font-normal">Sessions</th><th />
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => (
            <tr key={r.segment.market} className="border-t border-line">
              <td className="py-1.5 font-medium">{r.segment.market}</td>
              <td className="text-right text-ink-2">{pct(r.baseline_rate)}</td>
              <td className="text-right">{pct(r.current_rate)}</td>
              <td className="text-right"><Delta value={r.abs_change_pp} /></td>
              <td className="text-right"><Delta value={r.adj_change_pp} significant={(r.adj_q_value ?? 1) < 0.05} /></td>
              <td className="text-right text-ink-3">{pval(r.adj_q_value)}</td>
              <td className="text-right text-ink-3">{int(r.current_n)}</td>
              <td className="text-right"><Button variant="ghost" className="!px-2 !py-0.5 !text-xs" onClick={() => onInvestigate(r.segment.market)}>Investigate</Button></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Warnings({ warnings, onInvestigate }: { warnings: Warning[]; onInvestigate: (w: Warning) => void }) {
  if (!warnings.length) return <p className="text-[13px] text-ink-3">No data-quality issues detected in this scope.</p>;
  return (
    <ul className="space-y-2.5">
      {warnings.map((w, i) => (
        <li key={i} className="rounded-md border border-line p-2.5">
          <div className="flex items-start justify-between gap-2">
            <p className="text-[13px] font-medium">
              <Pill tone={w.severity === "high" ? "bad" : "neutral"}>{w.severity === "high" ? "⚠ High" : "ℹ Info"}</Pill>{" "}
              {w.title}
            </p>
            {w.segment && <Button variant="ghost" className="!px-2 !py-0.5 !text-xs" onClick={() => onInvestigate(w)}>Investigate</Button>}
          </div>
          <p className="mt-1 text-xs text-ink-3">{w.detail}</p>
        </li>
      ))}
    </ul>
  );
}

const SCAN_METRICS: Record<string, string> = {
  listing_to_contact_start: "Listing → contact start",
  contact_start_to_submit: "Contact start → submit",
  contact_to_viewing: "Contact → viewing",
  listing_to_contact: "Listing → contact",
};

function ScanCard({ onInvestigate }: { onInvestigate: (r: SegmentRow, metric: string) => void }) {
  const { filters, comparison } = useWorkspace();
  const [scans, setScans] = useState<Record<string, Scan> | null>(null);
  const [queries, setQueries] = useState<QueryRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const run = async () => {
    setLoading(true); setErr(null);
    try {
      const res = await api<{ scans: Record<string, Scan>; queries: QueryRecord[] }>("/detect", { filters, comparison });
      setScans(res.scans); setQueries(res.queries);
    } catch (e) { setErr((e as Error).message); } finally { setLoading(false); }
  };
  return (
    <Card title="Detect: which segments changed?"
      subtitle="Every single segment and key segment pair is tested at each funnel step. Changes are standardised over channel so mix shifts don't show up as false alarms; FDR-controlled."
      actions={<Button variant="primary" onClick={run} disabled={loading}>{loading ? "Scanning…" : scans ? "Re-run scan" : "Run segment scan"}</Button>}>
      {err && <ErrorBox error={err} />}
      {!scans && !loading && !err && (
        <EmptyState title="Scan the funnel for credible changes">
          Runs a few hundred segment comparisons in this scope and reports only those that survive multiple-comparison correction, plus a clearly-labelled watchlist.
        </EmptyState>
      )}
      {loading && <Spinner label="Testing segments at every funnel step…" />}
      {scans && (
        <div className="grid gap-4 md:grid-cols-2">
          {Object.entries(scans).map(([metric, s]) => (
            <div key={metric} className="rounded-md border border-line p-3">
              <div className="mb-2 flex items-baseline justify-between gap-2">
                <p className="text-[13px] font-semibold">{SCAN_METRICS[metric] ?? metric}</p>
                <p className="text-2xs text-ink-3">{s.n_flagged} of {s.n_tests} tests pass FDR&lt;5%</p>
              </div>
              {[...s.flagged, ...s.positive].length === 0 && <p className="text-xs text-ink-3">No segment passes the FDR threshold.</p>}
              <ul className="space-y-1">
                {[...s.flagged, ...s.positive].slice(0, 5).map((r) => (
                  <li key={JSON.stringify(r.segment)} className="flex items-center justify-between gap-2 text-xs">
                    <span className="truncate"><span className="font-medium">{segLabel(r.segment)}</span>{" "}
                      <span className="text-ink-3">{pct(r.baseline_rate)} → {pct(r.current_rate)}</span></span>
                    <span className="flex shrink-0 items-center gap-2">
                      <Delta value={r.scan_change_pp} /> <span className="num text-ink-3">q={pval(r.scan_q_value)}</span>
                      <Button variant="ghost" className="!px-1.5 !py-0 !text-2xs" onClick={() => onInvestigate(r, metric)}>Investigate</Button>
                    </span>
                  </li>
                ))}
              </ul>
              {s.watchlist.length > 0 && (
                <details className="mt-2 text-xs">
                  <summary className="cursor-pointer text-ink-3">Watchlist ({s.watchlist.length}) — not significant after correction</summary>
                  <ul className="mt-1 space-y-0.5">
                    {s.watchlist.slice(0, 5).map((r) => (
                      <li key={JSON.stringify(r.segment)} className="flex justify-between text-ink-3">
                        <span>{segLabel(r.segment)}</span><span className="num">{pp(r.scan_change_pp)} · p={pval(r.scan_p_value)}</span>
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </div>
          ))}
          <p className="text-2xs text-ink-3 md:col-span-2">{Object.values(scans)[0]?.method} {Object.values(scans)[0]?.multiple_comparison_note}</p>
        </div>
      )}
      <QueryPanel queries={queries} />
    </Card>
  );
}
