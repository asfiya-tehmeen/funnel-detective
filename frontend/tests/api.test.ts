import { api, ApiError } from "@/lib/api";

function mockFetch(status: number, body: unknown) {
  globalThis.fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status })) as unknown as typeof fetch;
}

describe("api client", () => {
  afterEach(() => vi.restoreAllMocks());

  it("returns parsed JSON and POSTs bodies", async () => {
    mockFetch(200, { ok: true });
    await expect(api("/overview", { filters: {} })).resolves.toEqual({ ok: true });
    const call = (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(call[0]).toBe("/api/overview");
    expect(call[1].method).toBe("POST");
  });

  it("turns FastAPI validation errors into readable messages", async () => {
    mockFetch(422, { detail: [{ loc: ["body", "filters", "market"], msg: "Value error, invalid market value(s) ['Narnia']" }] });
    await expect(api("/overview", {})).rejects.toThrow("filters.market: invalid market value(s) ['Narnia']");
  });

  it("surfaces 400 detail strings (e.g. rejected SQL)", async () => {
    mockFetch(400, { detail: "Only SELECT queries are allowed." });
    const err = (await api("/sql/run", { sql: "DROP" }).catch((e) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(400);
    expect(err.message).toBe("Only SELECT queries are allowed.");
  });

  it("explains when the backend is unreachable", async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new TypeError("fetch failed")) as unknown as typeof fetch;
    await expect(api("/meta")).rejects.toThrow(/Cannot reach the analytics API/);
  });
});
