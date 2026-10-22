"use client";

import clsx from "clsx";
import { useState, type ReactNode } from "react";
import type { HypStatus, QueryRecord } from "@/lib/types";
import { STATUS_LABEL, pp } from "@/lib/format";

export function Card({ title, subtitle, actions, children, className, dense }: {
  title?: ReactNode; subtitle?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; dense?: boolean;
}) {
  return (
    <section className={clsx("min-w-0 rounded-lg border border-line bg-raised", dense ? "p-4" : "p-5", className)}>
      {(title || actions) && (
        <header className="mb-4 flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
          <div className="min-w-0">
            {title && <h3 className="text-[13px] font-semibold tracking-tight text-ink">{title}</h3>}
            {subtitle && <p className="mt-0.5 text-xs text-ink-3">{subtitle}</p>}
          </div>
          {actions && <div className="flex max-w-full flex-wrap items-center gap-2">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

export function Button({ children, onClick, variant = "secondary", disabled, type = "button", className, title }: {
  children: ReactNode; onClick?: () => void; variant?: "primary" | "secondary" | "ghost"; disabled?: boolean;
  type?: "button" | "submit"; className?: string; title?: string;
}) {
  return (
    <button
      type={type}
      title={title}
      onClick={onClick}
      disabled={disabled}
      className={clsx(
        "inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-[13px] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50",
        variant === "primary" && "bg-accent text-white hover:bg-accent/90",
        variant === "secondary" && "border border-line bg-raised text-ink hover:bg-sunken",
        variant === "ghost" && "text-ink-2 hover:bg-sunken hover:text-ink",
        className,
      )}
    >
      {children}
    </button>
  );
}

const STATUS_STYLE: Record<HypStatus, { icon: string; cls: string }> = {
  supported: { icon: "●", cls: "text-good border-good/30 bg-good/5" },
  partially_supported: { icon: "◐", cls: "text-warn border-warn/30 bg-warn/5" },
  not_supported: { icon: "○", cls: "text-ink-3 border-line bg-sunken" },
  inconclusive: { icon: "◌", cls: "text-ink-3 border-line bg-sunken" },
};

export function StatusBadge({ status }: { status: HypStatus }) {
  const s = STATUS_STYLE[status];
  return (
    <span className={clsx("inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-2xs font-medium", s.cls)}>
      <span aria-hidden>{s.icon}</span>
      {STATUS_LABEL[status]}
    </span>
  );
}

export function Pill({ children, tone = "neutral", title }: {
  children: ReactNode; tone?: "neutral" | "accent" | "good" | "warn" | "bad"; title?: string;
}) {
  return (
    <span
      title={title}
      className={clsx(
        "inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-2xs font-medium",
        tone === "neutral" && "bg-sunken text-ink-2",
        tone === "accent" && "bg-accent-soft text-accent",
        tone === "good" && "bg-good/10 text-good",
        tone === "warn" && "bg-warn/10 text-warn",
        tone === "bad" && "bg-bad/10 text-bad",
      )}
    >
      {children}
    </span>
  );
}

/** A change in percentage points. Direction carried by arrow + sign, colour only reinforces it. */
export function Delta({ value, significant, digits = 1 }: { value: number | null | undefined; significant?: boolean; digits?: number }) {
  if (value === null || value === undefined) return <span className="text-ink-3">—</span>;
  const up = value > 0;
  const flat = Math.abs(value) < 0.05;
  return (
    <span className={clsx("num inline-flex items-center gap-0.5 font-medium",
      flat || significant === false ? "text-ink-2" : up ? "text-accent" : "text-bad")}>
      <span aria-hidden className="text-[10px]">{flat ? "→" : up ? "▲" : "▼"}</span>
      {pp(value, digits)}
      {significant === false && <span className="ml-1 text-2xs font-normal text-ink-3">n.s.</span>}
    </span>
  );
}

export function InfoTip({ text, children }: { text: string; children?: ReactNode }) {
  return (
    <span className="group relative inline-flex cursor-help items-center">
      {children ?? <span className="ml-1 inline-flex h-3.5 w-3.5 items-center justify-center rounded-full border border-line text-[9px] text-ink-3">i</span>}
      <span role="tooltip" className="pointer-events-none absolute left-1/2 top-full z-30 mt-1.5 hidden w-72 -translate-x-1/2 rounded-md border border-line bg-raised p-2.5 text-xs font-normal normal-case leading-snug tracking-normal text-ink-2 shadow-lg group-hover:block">
        {text}
      </span>
    </span>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 text-xs text-ink-3" role="status">
      <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-line border-t-accent" />
      {label ?? "Loading…"}
    </div>
  );
}

export function ErrorBox({ error, onRetry }: { error: string; onRetry?: () => void }) {
  return (
    <div className="flex items-start justify-between gap-3 rounded-md border border-bad/30 bg-bad/5 p-3 text-[13px] text-bad" role="alert">
      <span><span aria-hidden>⚠ </span>{error}</span>
      {onRetry && <Button variant="ghost" onClick={onRetry}>Retry</Button>}
    </div>
  );
}

export function EmptyState({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-line px-6 py-12 text-center">
      <p className="text-sm font-medium text-ink">{title}</p>
      {children && <div className="mt-1 max-w-md text-[13px] text-ink-3">{children}</div>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

/** Collapsible list of the exact SQL + parameters behind a view. */
export function QueryPanel({ queries, label = "SQL behind this view" }: { queries: QueryRecord[] | undefined; label?: string }) {
  const [open, setOpen] = useState(false);
  const [idx, setIdx] = useState(0);
  if (!queries || !queries.length) return null;
  const q = queries[Math.min(idx, queries.length - 1)];
  return (
    <div className="mt-3 border-t border-line pt-2">
      <button onClick={() => setOpen(!open)} className="text-xs font-medium text-ink-3 hover:text-ink">
        {open ? "▾" : "▸"} {label} <span className="text-ink-3">({queries.length} {queries.length === 1 ? "query" : "queries"})</span>
      </button>
      {open && (
        <div className="mt-2 grid gap-2 md:grid-cols-[200px_1fr]">
          <ul className="max-h-64 overflow-auto rounded border border-line bg-sunken text-xs">
            {queries.map((r, i) => (
              <li key={r.id}>
                <button onClick={() => setIdx(i)} className={clsx("w-full truncate px-2 py-1.5 text-left", i === idx ? "bg-accent-soft text-accent" : "text-ink-2 hover:bg-raised")}>
                  <span className="font-mono">{r.name}</span>
                  <span className="ml-1 text-ink-3">· {r.row_count} rows</span>
                </button>
              </li>
            ))}
          </ul>
          <div className="min-w-0">
            <pre className="sql max-h-64 overflow-auto rounded border border-line bg-sunken p-3 text-ink">{q.sql}</pre>
            <p className="mt-1 text-2xs text-ink-3">
              Parameters (bound, never interpolated): <span className="font-mono">[{q.params.join(", ")}]</span> · {q.duration_ms} ms · id {q.id}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

export function Segmented<T extends string>({ value, options, onChange, size = "md" }: {
  value: T; options: { value: T; label: string }[]; onChange: (v: T) => void; size?: "sm" | "md";
}) {
  return (
    <div className="inline-flex max-w-full flex-wrap rounded-md border border-line bg-sunken p-0.5">
      {options.map((o) => (
        <button key={o.value} onClick={() => onChange(o.value)}
          className={clsx("rounded px-2.5 font-medium transition-colors", size === "sm" ? "py-0.5 text-xs" : "py-1 text-[13px]",
            o.value === value ? "bg-raised text-ink shadow-sm" : "text-ink-3 hover:text-ink")}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-ink-2">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-2xs text-ink-3">{hint}</span>}
    </label>
  );
}

export const inputCls =
  "w-full rounded-md border border-line bg-raised px-2.5 py-1.5 text-[13px] text-ink num placeholder:text-ink-3 focus:border-accent focus:outline-none";
