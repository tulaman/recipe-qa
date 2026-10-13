import { ask, type AskResponse, type AskResult, type Citation, type RefusalReason } from "./api";
import "./style.css";

const MAX_LENGTH = 1000;

const REFUSAL_LABELS: Record<RefusalReason, string> = {
  out_of_corpus: "Not covered by these recipes",
  out_of_domain: "Not a cooking question",
  safety: "Allergen or safety guarantee",
};

const ERROR_MESSAGES: Record<string, string> = {
  rate_limited: "Too many questions in a short time. Please wait a minute and try again.",
  upstream_unavailable: "The language model is unavailable right now. Please try again shortly.",
  upstream_invalid: "The model did not produce a usable answer. Please try again or rephrase.",
  internal_error: "Something went wrong on our side. Please try again.",
};

const form = document.querySelector<HTMLFormElement>("#ask-form")!;
const input = document.querySelector<HTMLTextAreaElement>("#question")!;
const submit = document.querySelector<HTMLButtonElement>("#submit")!;
const counter = document.querySelector<HTMLSpanElement>("#counter")!;
const result = document.querySelector<HTMLElement>("#result")!;

// All server text is inserted with textContent, never as HTML.
function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className?: string,
  text?: string,
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function citationList(citations: Citation[], heading: string): HTMLElement {
  const wrap = el("div", "citations");
  wrap.append(el("h2", "citations-heading", heading));
  const list = el("ul");
  for (const c of citations) {
    const item = el("li");
    const link = el("a", undefined, c.title);
    link.href = c.url;
    link.target = "_blank";
    link.rel = "noopener";
    item.append(link);
    list.append(item);
  }
  wrap.append(list);
  return wrap;
}

function metaLine(requestId: string | null, extra?: string): HTMLElement {
  const line = el("p", "meta");
  line.textContent = [requestId ? `Request ID ${requestId}` : null, extra]
    .filter(Boolean)
    .join(" · ");
  return line;
}

function show(...nodes: HTMLElement[]): void {
  result.replaceChildren(...nodes);
  result.hidden = false;
}

function renderAnswer(body: AskResponse): void {
  const card = el("article", "card card-answer");
  card.append(el("p", "answer", body.answer ?? ""));
  card.append(citationList(body.citations, body.citations.length > 1 ? "Sources" : "Source"));
  card.append(metaLine(body.meta.request_id, `${body.meta.model} · ${body.meta.latency_ms} ms`));
  show(card);
}

function renderRefusal(body: AskResponse): void {
  const reason = body.refusal_reason ?? "out_of_corpus";
  const card = el("article", `card card-refusal card-${reason}`);
  card.append(el("p", "badge", REFUSAL_LABELS[reason]));
  card.append(el("p", "message", body.refusal_message ?? ""));
  if (body.citations.length > 0) {
    card.append(citationList(body.citations, "Check the ingredient list yourself"));
  }
  card.append(metaLine(body.meta.request_id, `${body.meta.latency_ms} ms`));
  show(card);
}

function renderError(res: Extract<AskResult, { kind: "error" }>): void {
  const card = el("article", "card card-error");
  card.setAttribute("role", "alert");
  card.append(el("p", "badge", res.status ? `Error ${res.status}` : "Connection error"));
  const text =
    res.code === "invalid_request" ? res.message : (ERROR_MESSAGES[res.code] ?? res.message);
  card.append(el("p", "message", text));
  card.append(metaLine(res.requestId, res.code));
  show(card);
}

function renderLoading(): void {
  const card = el("article", "card card-loading");
  card.setAttribute("aria-busy", "true");
  card.append(el("span", "spinner"), el("span", undefined, "Reading the recipes…"));
  show(card);
}

const chips = [...document.querySelectorAll<HTMLButtonElement>(".chip")];

// One request at a time: every control reads this flag, so no handler can
// re-enable the form while a request is in flight.
let busy = false;

function syncControls(): void {
  const length = [...input.value.trim()].length;
  counter.textContent = `${[...input.value].length} / ${MAX_LENGTH}`;
  submit.disabled = busy || length === 0 || length > MAX_LENGTH;
  submit.textContent = busy ? "Asking…" : "Ask";
  input.readOnly = busy;
  for (const chip of chips) chip.disabled = busy;
}

async function submitQuestion(): Promise<void> {
  const question = input.value.trim();
  if (busy || !question) return;
  busy = true;
  syncControls();
  renderLoading();
  try {
    const res = await ask(question);
    if (res.kind === "error") renderError(res);
    else if (res.body.refused) renderRefusal(res.body);
    else renderAnswer(res.body);
  } finally {
    busy = false;
    syncControls();
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  void submitQuestion();
});

input.addEventListener("input", () => syncControls());
input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    if (!submit.disabled) form.requestSubmit();
  }
});

for (const chip of chips) {
  chip.addEventListener("click", () => {
    if (busy) return;
    input.value = chip.textContent ?? "";
    syncControls();
    form.requestSubmit();
  });
}

syncControls();
input.focus();
