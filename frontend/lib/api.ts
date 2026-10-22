// Thin typed client. All calls go to /api/* on the same origin; Next.js proxies them to FastAPI.

export class ApiError extends Error {
  constructor(message: string, public status: number) {
    super(message);
  }
}

function describe(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d) => {
        const loc = Array.isArray(d?.loc) ? d.loc.filter((x: unknown) => x !== "body").join(".") : "";
        const msg = String(d?.msg ?? "invalid value").replace(/^Value error, /, "");
        return loc ? `${loc}: ${msg}` : msg;
      })
      .join("; ");
  }
  return "Unexpected error";
}

export async function api<T>(path: string, body?: unknown, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api${path}`, {
      method: body === undefined ? "GET" : "POST",
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      ...init,
    });
  } catch {
    throw new ApiError("Cannot reach the analytics API. Is the backend running on port 8000?", 0);
  }
  const text = await res.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }
  if (!res.ok) {
    const detail = (data as { detail?: unknown } | null)?.detail;
    const fallback = res.status >= 500 ? "The analytics API returned an error (see the backend log)." : `Request failed (${res.status})`;
    throw new ApiError(detail !== undefined ? describe(detail) : fallback, res.status);
  }
  return data as T;
}
