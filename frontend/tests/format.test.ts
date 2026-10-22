import { cleanFilters, filtersLabel, pct, pp, pval, rel, segLabel } from "@/lib/format";

describe("format helpers", () => {
  it("formats rates and missing values", () => {
    expect(pct(0.0847)).toBe("8.5%");
    expect(pct(0.0847, 2)).toBe("8.47%");
    expect(pct(null)).toBe("—");
  });

  it("formats percentage-point changes with an explicit sign", () => {
    expect(pp(-2.37)).toBe("−2.4pp");
    expect(pp(1.04)).toBe("+1.0pp");
    expect(pp(0.01)).toBe("±0.0pp");
    expect(pp(undefined)).toBe("—");
  });

  it("formats relative changes and p-values", () => {
    expect(rel(-21.8)).toBe("−22%");
    expect(pval(0.0004)).toBe("<0.001");
    expect(pval(0.0432)).toBe("0.043");
  });

  it("labels segments and filters", () => {
    expect(segLabel({ market: "Riyadh", device: "mobile_web" })).toBe("Riyadh · Mobile web");
    expect(segLabel({})).toBe("All traffic");
    expect(filtersLabel({ market: ["Dubai"], price_band: ["luxury"] })).toBe("Dubai · Luxury");
    expect(cleanFilters({ market: ["Dubai"], device: [] })).toEqual({ market: ["Dubai"] });
  });
});
