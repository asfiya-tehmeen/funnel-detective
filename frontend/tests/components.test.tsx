import { fireEvent, render, screen } from "@testing-library/react";
import { ResultView } from "@/components/stages/measure";
import { Delta, QueryPanel, StatusBadge } from "@/components/ui";
import type { ResultsAnalysis } from "@/lib/types";

describe("UI primitives", () => {
  it("status badge pairs an icon with a text label (never colour alone)", () => {
    render(<StatusBadge status="partially_supported" />);
    expect(screen.getByText("Partially supported")).toBeInTheDocument();
    expect(screen.getByText("◐")).toBeInTheDocument();
  });

  it("delta shows direction by sign and marks non-significant changes", () => {
    render(<Delta value={-2.37} significant={false} />);
    expect(screen.getByText("−2.4pp")).toBeInTheDocument();
    expect(screen.getByText("n.s.")).toBeInTheDocument();
  });

  it("query panel reveals the exact SQL and bound parameters", () => {
    render(<QueryPanel queries={[{ id: "q_1", name: "metric_comparison", sql: "SELECT 1 WHERE market IN (?)", params: ["Riyadh"], row_count: 2, duration_ms: 3 }]} />);
    expect(screen.queryByText(/SELECT 1/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByText(/SQL behind this view/));
    expect(screen.getByText(/SELECT 1 WHERE market IN/)).toBeInTheDocument();
    expect(screen.getByText(/\[Riyadh\]/)).toBeInTheDocument();
  });
});

const result: ResultsAnalysis = {
  source: "simulated", outcome_type: "simulated outcome", metric_label: "Listing → contact",
  test: { baseline_rate: 0.08, current_rate: 0.088, baseline_n: 20000, current_n: 20000, baseline_x: 1600, current_x: 1760,
    abs_change_pp: 0.8, rel_change_pct: 10, diff_ci_pp: [0.27, 1.33], baseline_ci: [0.076, 0.084], current_ci: [0.084, 0.092],
    test: "two-proportion z-test (pooled)", p_value: 0.003, significant: true, assumption_note: "", p_value_note: "" },
  relative_lift_pct: 10, srm: { chi2: 0.1, p_value: 0.75, mismatch: false, expected_split: 0.5 }, guardrails: [],
  decision: "ship", reasons: ["Primary metric improved."], decision_rule: "rule", p_value_note: "note",
  simulation: { true_control_rate: 0.08, true_treatment_rate: 0.088, true_relative_lift: 0.1, seed: 7,
    note: "Simulated outcome from a binomial model — not an observed result." },
};

describe("experiment result view", () => {
  it("labels simulated outcomes and shows the decision", () => {
    render(<ResultView r={result} mde={0.8} />);
    expect(screen.getByText("Ship")).toBeInTheDocument();
    expect(screen.getByText("simulated outcome")).toBeInTheDocument();
    expect(screen.getByText(/not an observed result/)).toBeInTheDocument();
  });
});
