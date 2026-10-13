// Client for POST /ask. Types mirror SPEC §2; GET /schema is the source of truth.

export type RefusalReason = "out_of_corpus" | "out_of_domain" | "safety";

export interface Citation {
  title: string;
  url: string;
}

export interface AskResponse {
  answer: string | null;
  citations: Citation[];
  refused: boolean;
  refusal_reason: RefusalReason | null;
  refusal_message: string | null;
  meta: { request_id: string; model: string; corpus_version: string; latency_ms: number };
}

export interface ApiError {
  error: { code: string; message: string; request_id: string };
}

export type AskResult =
  | { kind: "ok"; body: AskResponse }
  | { kind: "error"; status: number; code: string; message: string; requestId: string | null };

// Longer than the server's 12 s deadline, so the server's 503 wins.
const CLIENT_TIMEOUT_MS = 20_000;

export async function ask(question: string): Promise<AskResult> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), CLIENT_TIMEOUT_MS);
  let response: Response;
  try {
    response = await fetch("/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
      signal: controller.signal,
    });
  } catch {
    return {
      kind: "error",
      status: 0,
      code: "network_error",
      message: "Could not reach the service. Check your connection and try again.",
      requestId: null,
    };
  } finally {
    clearTimeout(timer);
  }

  const requestId = response.headers.get("X-Request-ID");
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    // Non-JSON body (e.g. a proxy error page): handled below.
  }
  if (response.ok && body && typeof body === "object" && "refused" in body) {
    return { kind: "ok", body: body as AskResponse };
  }
  const err = (body as ApiError | null)?.error;
  return {
    kind: "error",
    status: response.status,
    code: err?.code ?? "unexpected_response",
    message: err?.message ?? `Unexpected response (HTTP ${response.status}).`,
    requestId: err?.request_id ?? requestId,
  };
}
