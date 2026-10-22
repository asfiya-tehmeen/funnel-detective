"use client";

import {
  Area, Bar, BarChart, CartesianGrid, Cell, ComposedChart, ErrorBar, Line, ReferenceArea, ReferenceLine,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import type { Comparison, FunnelStep, TrendPoint } from "@/lib/types";
import { int, pct, pp, shortDate } from "@/lib/format";

const C = {
  current: "var(--chart-current)", baseline: "var(--chart-baseline)", grid: "var(--chart-grid)",
  axis: "var(--chart-axis)", neg: "var(--chart-neg)", pos: "var(--chart-pos)",
};
const axisProps = { stroke: C.axis, tick: { fill: C.axis, fontSize: 11 }, tickLine: false, axisLine: { stroke: C.grid } };

function TipBox({ title, rows }: { title: string; rows: [string, string][] }) {
  return (
    <div className="rounded-md border border-line bg-raised px-3 py-2 text-xs shadow-lg">
      <p className="mb-1 font-medium text-ink">{title}</p>
      {rows.map(([k, v]) => (
        <p key={k} className="flex justify-between gap-4 text-ink-2"><span>{k}</span><span className="num text-ink">{v}</span></p>
      ))}
    </div>
  );
}

/** Weekly rate with 95% interval band; baseline/current windows shaded. */
export function TrendChart({ points, comparison, height = 220 }: { points: TrendPoint[]; comparison: Comparison; height?: number }) {
  const data = points.map((p) => ({
    ...p, band: p.ci_low !== null && p.ci_high !== null ? [p.ci_low, p.ci_high] : null,
  }));
  return (
    <div style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={C.grid} vertical={false} />
          <ReferenceArea x1={weekOf(comparison.baseline.start, data)} x2={weekOf(comparison.current.start, data)}
            fill={C.baseline} fillOpacity={0.1} ifOverflow="extendDomain" />
          <ReferenceArea x1={weekOf(comparison.current.start, data)} x2={data[data.length - 1]?.bucket}
            fill={C.current} fillOpacity={0.07} ifOverflow="extendDomain" />
          <XAxis dataKey="bucket" tickFormatter={shortDate} {...axisProps} minTickGap={24} />
          <YAxis tickFormatter={(v) => pct(v, 0)} width={44} {...axisProps} domain={["auto", "auto"]} />
          <Area dataKey="band" stroke="none" fill={C.current} fillOpacity={0.12} isAnimationActive={false} />
          <Line dataKey="rate" stroke={C.current} strokeWidth={2} dot={false} isAnimationActive={false}
            activeDot={{ r: 4, stroke: "rgb(var(--raised))", strokeWidth: 2 }} />
          <Tooltip cursor={{ stroke: C.axis, strokeWidth: 1 }} content={({ active, payload }) => {
            if (!active || !payload?.length) return null;
            const p = payload[0].payload as TrendPoint;
            return <TipBox title={`Week of ${shortDate(p.bucket)}`} rows={[["Rate", pct(p.rate, 2)],
              ["95% CI", `${pct(p.ci_low)} – ${pct(p.ci_high)}`], ["Numerator", int(p.numerator)], ["Denominator", int(p.denominator)]]} />;
          }} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

function weekOf(iso: string, data: { bucket: string }[]): string | undefined {
  // Map a date onto the weekly bucket that contains it (buckets are Monday starts).
  let best: string | undefined;
  for (const d of data) if (d.bucket <= iso) best = d.bucket;
  return best ?? data[0]?.bucket;
}

/** Baseline vs current sessions per funnel step (grouped horizontal bars). */
export function FunnelChart({ baseline, current }: { baseline: FunnelStep[]; current: FunnelStep[] }) {
  const data = current.map((s, i) => ({
    label: s.label, current: s.sessions, baseline: baseline[i]?.sessions ?? 0,
    conv: s.step_conversion, convBase: baseline[i]?.step_conversion ?? null,
  }));
  return (
    <div style={{ height: 230 }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} layout="vertical" margin={{ top: 0, right: 16, bottom: 0, left: 0 }} barGap={2} barCategoryGap="22%">
          <CartesianGrid stroke={C.grid} horizontal={false} />
          <XAxis type="number" tickFormatter={(v) => int(v)} {...axisProps} />
          <YAxis type="category" dataKey="label" width={118} {...axisProps} />
          <Bar dataKey="baseline" name="Baseline" fill={C.baseline} radius={[0, 4, 4, 0]} isAnimationActive={false} />
          <Bar dataKey="current" name="Current" fill={C.current} radius={[0, 4, 4, 0]} isAnimationActive={false} />
          <Tooltip cursor={{ fill: "rgb(var(--sunken))" }} content={({ active, payload }) => {
            if (!active || !payload?.length) return null;
            const d = payload[0].payload as (typeof data)[number];
            return <TipBox title={d.label} rows={[["Baseline sessions", int(d.baseline)], ["Current sessions", int(d.current)],
              ["Step conversion (baseline)", pct(d.convBase)], ["Step conversion (current)", pct(d.conv)]]} />;
          }} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export interface ChangeDatum { label: string; change: number | null; lo?: number | null; hi?: number | null; n?: number }

/** Diverging bars for a change in pp with 95% interval whiskers. Decline red, increase blue (+ sign in labels). */
export function ChangeBars({ data, height }: { data: ChangeDatum[]; height?: number }) {
  const rows = data.filter((d) => d.change !== null).map((d) => ({
    ...d, err: d.lo !== undefined && d.lo !== null && d.hi !== undefined && d.hi !== null
      ? [d.change! - d.lo, d.hi - d.change!] : [0, 0],
  }));
  const h = height ?? Math.max(120, rows.length * 30 + 40);
  return (
    <div style={{ height: h }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 20, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={C.grid} horizontal={false} />
          <XAxis type="number" tickFormatter={(v) => `${v > 0 ? "+" : ""}${v}pp`} {...axisProps} />
          <YAxis type="category" dataKey="label" width={130} {...axisProps} />
          <ReferenceLine x={0} stroke={C.axis} />
          <Bar dataKey="change" radius={4} isAnimationActive={false} barSize={14}>
            {rows.map((r) => <Cell key={r.label} fill={(r.change ?? 0) < 0 ? C.neg : C.pos} />)}
            <ErrorBar dataKey="err" direction="x" width={4} stroke={C.axis} strokeWidth={1} />
          </Bar>
          <Tooltip cursor={{ fill: "rgb(var(--sunken))" }} content={({ active, payload }) => {
            if (!active || !payload?.length) return null;
            const d = payload[0].payload as ChangeDatum;
            return <TipBox title={d.label} rows={[["Change", pp(d.change, 2)],
              ["95% CI", `${pp(d.lo ?? null, 2)} to ${pp(d.hi ?? null, 2)}`], ...(d.n ? [["Current n", int(d.n)] as [string, string]] : [])]} />;
          }} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Baseline vs current share of the denominator per segment. */
export function ShareBars({ data }: { data: { label: string; baseline: number; current: number }[] }) {
  return (
    <div style={{ height: Math.max(140, data.length * 34 + 30) }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} layout="vertical" margin={{ top: 0, right: 16, bottom: 0, left: 0 }} barGap={2}>
          <CartesianGrid stroke={C.grid} horizontal={false} />
          <XAxis type="number" tickFormatter={(v) => pct(v, 0)} {...axisProps} />
          <YAxis type="category" dataKey="label" width={118} {...axisProps} />
          <Bar dataKey="baseline" fill={C.baseline} radius={[0, 4, 4, 0]} barSize={9} isAnimationActive={false} />
          <Bar dataKey="current" fill={C.current} radius={[0, 4, 4, 0]} barSize={9} isAnimationActive={false} />
          <Tooltip cursor={{ fill: "rgb(var(--sunken))" }} content={({ active, payload }) => {
            if (!active || !payload?.length) return null;
            const d = payload[0].payload as { label: string; baseline: number; current: number };
            return <TipBox title={d.label} rows={[["Baseline share", pct(d.baseline)], ["Current share", pct(d.current)]]} />;
          }} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/** One difference with its CI against zero and an optional MDE marker (experiment readout). */
export function IntervalPlot({ value, lo, hi, mde }: { value: number; lo: number; hi: number; mde?: number | null }) {
  const span = Math.max(Math.abs(lo), Math.abs(hi), Math.abs(mde ?? 0), 0.5) * 1.3;
  const x = (v: number) => `${((v + span) / (2 * span)) * 100}%`;
  return (
    <div className="relative h-12 select-none" aria-label={`Difference ${pp(value, 2)}, 95% CI ${pp(lo, 2)} to ${pp(hi, 2)}`}>
      <div className="absolute inset-x-0 top-6 h-px bg-line" />
      <div className="absolute top-2 h-8 w-px bg-ink-3" style={{ left: x(0) }} />
      <span className="absolute top-10 -translate-x-1/2 text-2xs text-ink-3" style={{ left: x(0) }}>0</span>
      {mde ? (<>
        <div className="absolute top-3 h-6 w-px bg-warn" style={{ left: x(mde) }} />
        <span className="absolute -top-0.5 -translate-x-1/2 text-2xs text-warn" style={{ left: x(mde) }}>MDE</span>
      </>) : null}
      <div className="absolute top-[22px] h-[5px] rounded-full bg-accent/40" style={{ left: x(lo), width: `calc(${x(hi)} - ${x(lo)})` }} />
      <div className="absolute top-[18px] h-3 w-3 -translate-x-1/2 rounded-full border-2 border-raised bg-accent" style={{ left: x(value) }} />
    </div>
  );
}
