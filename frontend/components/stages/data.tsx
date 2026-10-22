"use client";

import { useState } from "react";
import { Button, Card, ErrorBox, Pill, Segmented, Spinner, inputCls } from "@/components/ui";
import { useApi, useWorkspace } from "@/components/workspace-context";
import { api } from "@/lib/api";
import { humanize } from "@/lib/format";
import type { Investigation, MetricDef } from "@/lib/types";

interface Dictionary {
  event_columns: Record<string, string>; session_columns: Record<string, string>; tables: Record<string, string>;
  metrics: Record<string, MetricDef>; pipeline_sql: Record<string, string>; disclaimer: string;
}
interface Catalog { queries: Record<string, { description: string; example_sql: string; example_params: string[] }>; tools: { name: string; description: string }[] }
interface SqlResult { columns: { name: string; type: string }[]; rows: Record<string, unknown>[]; row_count: number; truncated: boolean;
  row_limit?: number; duration_ms?: number; executed_sql?: string }
interface AuditData { investigations: { id: string; created_at: string; question: string; mode: string; intent: string; leading_hypothesis: string; n_tool_calls: number; n_queries: number; narrative_source: string }[];
  sql_runs: { id: number; created_at: string; kind: string; sql_text: string; status: string; row_count: number | null; error: string | null }[] }

export function DataView() {
  const [tab, setTab] = useState<"dictionary" | "queries" | "console" | "audit">("dictionary");
  return (
    <div className="space-y-5">
      <Segmented value={tab} onChange={setTab} options={[{ value: "dictionary", label: "Data dictionary" },
        { value: "queries", label: "Approved queries & tools" }, { value: "console", label: "SQL console" }, { value: "audit", label: "Audit trail" }]} />
      {tab === "dictionary" && <DictionaryTab />}
      {tab === "queries" && <QueriesTab />}
      {tab === "console" && <ConsoleTab />}
      {tab === "audit" && <AuditTab />}
    </div>
  );
}

function DictionaryTab() {
  const { data, error } = useApi<Dictionary>("/dictionary", undefined);
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Spinner />;
  return (
    <div className="grid gap-5 lg:grid-cols-2">
      <Card title="Metric definitions" subtitle="Session-based. The SQL expressions below are exactly what every query uses.">
        <ul className="divide-y divide-line">
          {Object.entries(data.metrics).map(([k, m]) => (
            <li key={k} className="py-2.5 text-[13px]">
              <p className="font-medium">{m.label} <span className="font-mono text-2xs text-ink-3">{k}</span></p>
              <p className="text-xs text-ink-2">{m.description}</p>
              <p className="mt-1 text-xs"><span className="text-ink-3">Numerator:</span> {m.numerator} <code className="sql text-2xs text-ink-3">{m.numerator_sql}</code></p>
              <p className="text-xs"><span className="text-ink-3">Denominator:</span> {m.denominator} <code className="sql text-2xs text-ink-3">{m.denominator_sql}</code></p>
            </li>
          ))}
        </ul>
      </Card>
      <div className="space-y-5">
        <Card title="Tables">
          <ul className="space-y-1.5 text-[13px]">{Object.entries(data.tables).map(([k, v]) => <li key={k}><code className="font-mono text-xs">{k}</code> — <span className="text-ink-2">{v}</span></li>)}</ul>
        </Card>
        <Card title="Event columns">
          <dl className="space-y-1 text-xs">{Object.entries(data.event_columns).map(([k, v]) => <div key={k} className="grid grid-cols-[120px_1fr] gap-2"><dt className="font-mono">{k}</dt><dd className="text-ink-2">{v}</dd></div>)}</dl>
        </Card>
        <Card title="Session columns">
          <dl className="space-y-1 text-xs">{Object.entries(data.session_columns).map(([k, v]) => <div key={k} className="grid grid-cols-[1fr_1.4fr] gap-2"><dt className="font-mono">{k}</dt><dd className="text-ink-2">{v}</dd></div>)}</dl>
        </Card>
      </div>
      <Card className="lg:col-span-2" title="Load pipeline SQL" subtitle="Raw parquet → quarantine → de-duplicated events → session table. Runs once at startup; afterwards DuckDB external access is disabled and locked.">
        {Object.entries(data.pipeline_sql).map(([k, sql]) => (
          <details key={k} className="border-t border-line py-2"><summary className="cursor-pointer font-mono text-xs">{k}</summary>
            <pre className="sql mt-2 max-h-80 overflow-auto rounded bg-sunken p-3">{sql.trim()}</pre></details>
        ))}
      </Card>
    </div>
  );
}

function ResultTable({ r }: { r: SqlResult }) {
  return (
    <div>
      <div className="max-h-[420px] overflow-auto rounded border border-line">
        <table className="num w-full text-xs">
          <thead className="sticky top-0 bg-sunken"><tr>{r.columns.map((c) => <th key={c.name} className="px-2 py-1.5 text-left font-medium">{c.name}<span className="ml-1 font-normal text-ink-3">{c.type}</span></th>)}</tr></thead>
          <tbody>{r.rows.map((row, i) => (
            <tr key={i} className="border-t border-line">{r.columns.map((c) => <td key={c.name} className="whitespace-nowrap px-2 py-1">{fmtCell(row[c.name])}</td>)}</tr>))}</tbody>
        </table>
      </div>
      <p className="mt-1 text-2xs text-ink-3">{r.row_count} rows{r.truncated ? ` (truncated at ${r.row_limit ?? 500})` : ""}{r.duration_ms !== undefined ? ` · ${r.duration_ms} ms` : ""}</p>
    </div>
  );
}

const fmtCell = (v: unknown) => (v === null || v === undefined ? <span className="text-ink-3">null</span>
  : typeof v === "number" ? (Number.isInteger(v) ? v.toLocaleString("en-US") : v.toFixed(4)) : String(v));

function QueriesTab() {
  const { filters, comparison } = useWorkspace();
  const { data, error } = useApi<Catalog>("/sql/catalog", undefined);
  const [result, setResult] = useState<{ name: string; r: SqlResult & { query: { sql: string; params: string[] } } } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Spinner />;
  const run = async (name: string) => {
    setErr(null);
    try {
      const r = await api<SqlResult & { query: { sql: string; params: string[] } }>("/sql/approved", {
        name, filters, comparison, group_by: ["metric_comparison", "quality_by_segment"].includes(name) ? ["market"] : [] });
      setResult({ name, r });
    } catch (e) { setErr((e as Error).message); }
  };
  return (
    <div className="grid gap-5 lg:grid-cols-2">
      <Card title="Approved, parameterised queries" subtitle="The only SQL the investigation engine can run. Values are bound as parameters; identifiers come from whitelists.">
        <ul className="divide-y divide-line">
          {Object.entries(data.queries).map(([k, q]) => (
            <li key={k} className="py-2.5">
              <div className="flex items-center justify-between gap-2">
                <p className="font-mono text-xs font-medium">{k}</p>
                <Button variant="ghost" className="!py-0.5 !text-xs" onClick={() => run(k)}>Run with current scope</Button>
              </div>
              <p className="text-xs text-ink-2">{q.description}</p>
              <details className="mt-1"><summary className="cursor-pointer text-2xs text-ink-3">Example SQL</summary>
                <pre className="sql mt-1 max-h-64 overflow-auto rounded bg-sunken p-2">{q.example_sql}</pre>
                <p className="text-2xs text-ink-3">params: [{q.example_params.join(", ")}]</p></details>
            </li>
          ))}
        </ul>
      </Card>
      <div className="space-y-5">
        {err && <ErrorBox error={err} />}
        {result && (
          <Card title={`Result: ${result.name}`}>
            <pre className="sql mb-2 max-h-48 overflow-auto rounded bg-sunken p-2">{result.r.query.sql}</pre>
            <p className="mb-2 text-2xs text-ink-3">params: [{result.r.query.params.join(", ")}]</p>
            <ResultTable r={result.r} />
          </Card>
        )}
        <Card title="Approved analytical tools" subtitle="What the AI planner may request. It cannot run SQL directly.">
          <ul className="space-y-1.5 text-xs">{data.tools.map((t) => <li key={t.name}><code className="font-mono">{t.name}</code> — <span className="text-ink-2">{t.description}</span></li>)}</ul>
        </Card>
      </div>
    </div>
  );
}

const EXAMPLES = [
  "SELECT market, device,\n  COUNT(*) FILTER (WHERE has_listing) AS listing_sessions,\n  AVG(CASE WHEN has_contact THEN 1.0 ELSE 0 END) FILTER (WHERE has_listing) AS listing_to_contact\nFROM sessions\nWHERE session_date >= DATE '2026-07-20'\nGROUP BY ALL\nORDER BY listing_to_contact",
  "-- Ghost contact events: submits with no lead_id, by client version\nSELECT client_version, COUNT(*) AS events\nFROM events\nWHERE event_type = 'contact_submitted' AND lead_id IS NULL\nGROUP BY ALL ORDER BY events DESC",
  "SELECT quarantine_reason, COUNT(*) FROM quarantine GROUP BY ALL",
];

function ConsoleTab() {
  const [sql, setSql] = useState(EXAMPLES[0]);
  const [r, setR] = useState<SqlResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setBusy(true); setErr(null);
    try { setR(await api<SqlResult>("/sql/run", { sql, limit: 200 })); } catch (e) { setErr((e as Error).message); setR(null); } finally { setBusy(false); }
  };
  return (
    <Card title="Restricted SQL console" subtitle="Read-only SELECT over sessions, events, events_all and quarantine. One statement, ≤200 rows shown, 5-second limit; files, network and settings are blocked.">
      <div className="mb-2 flex flex-wrap gap-1.5">{EXAMPLES.map((e, i) => <Button key={i} variant="ghost" className="!py-0.5 !text-xs" onClick={() => setSql(e)}>Example {i + 1}</Button>)}</div>
      <textarea aria-label="SQL" value={sql} onChange={(e) => setSql(e.target.value)} rows={8} spellCheck={false}
        className={`${inputCls} sql`} onKeyDown={(e) => { if ((e.metaKey || e.ctrlKey) && e.key === "Enter") run(); }} />
      <div className="mt-2 flex items-center gap-2"><Button variant="primary" onClick={run} disabled={busy}>{busy ? "Running…" : "Run (Ctrl+Enter)"}</Button>
        <Pill>Every run is recorded in the audit trail</Pill></div>
      {err && <div className="mt-3"><ErrorBox error={err} /></div>}
      {r && <div className="mt-3"><ResultTable r={r} /></div>}
    </Card>
  );
}

function AuditTab() {
  const { setInvestigation, setStage } = useWorkspace();
  const { data, error, reload } = useApi<AuditData>("/audit", undefined);
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  if (!data) return <Spinner />;
  const open = async (id: string) => {
    setInvestigation(await api<Investigation>(`/investigations/${id}`));
    setStage("challenge");
  };
  return (
    <div className="grid gap-5 lg:grid-cols-2">
      <Card title="Investigations" subtitle="Inputs, plan, tool calls, evidence and narrative are stored for every run" actions={<Button variant="ghost" onClick={reload}>Refresh</Button>}>
        {data.investigations.length === 0 && <p className="text-xs text-ink-3">No investigations yet.</p>}
        <ul className="divide-y divide-line">
          {data.investigations.map((i) => (
            <li key={i.id} className="flex items-start justify-between gap-3 py-2 text-xs">
              <div className="min-w-0"><p className="truncate text-[13px] font-medium">{i.question}</p>
                <p className="text-ink-3">{new Date(i.created_at).toLocaleString()} · {humanize(i.intent)} · {i.mode} · {i.n_tool_calls} tools · {i.n_queries} queries · lead {i.leading_hypothesis}</p></div>
              <Button variant="ghost" className="!py-0.5 !text-xs" onClick={() => open(i.id)}>Open</Button>
            </li>
          ))}
        </ul>
      </Card>
      <Card title="SQL runs" subtitle="Console and approved-query runs, including rejected queries">
        {data.sql_runs.length === 0 && <p className="text-xs text-ink-3">No SQL runs yet.</p>}
        <ul className="divide-y divide-line">
          {data.sql_runs.map((s) => (
            <li key={s.id} className="py-2 text-xs">
              <p className="flex items-center gap-2"><Pill tone={s.status === "ok" ? "good" : "bad"}>{s.status}</Pill><span className="text-ink-3">{s.kind} · {new Date(s.created_at).toLocaleString()}{s.row_count !== null ? ` · ${s.row_count} rows` : ""}</span></p>
              <pre className="sql mt-1 max-h-24 overflow-auto whitespace-pre-wrap rounded bg-sunken p-1.5">{s.sql_text}</pre>
              {s.error && <p className="mt-0.5 text-bad">{s.error}</p>}
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}
