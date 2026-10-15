// LifeNest AI chat client. Every figure shown comes from API `cards` / `computations`, never from parsing reply text.
// All server-provided strings are inserted as text nodes via h(); no HTML string is ever parsed.
import { balanceChart } from "./chart.js";

const CFG = window.LIFENEST_CONFIG;
const LOCALES = { en: "en-MY", ms: "ms-MY", zh: "zh-CN" };
const ALLOWED_TYPES = ["image/png", "image/jpeg", "image/webp", "application/pdf"];
const SCALAR_FIELDS = new Set(["person.age", "person.dependants", "employment.months_in_service", "existing_housing_loans"]);

let STR = {};
let lang = localStorage.getItem("lifenest.lang") || "en";
const state = { sessionId: null, profileStatus: "EMPTY", busy: false };
const $ = (id) => document.getElementById(id);

// --- helpers -----------------------------------------------------------------------------------------

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : String(v));
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : String(c));
  }
  return el;
}

function t(key, vars = {}) {
  const s = STR[lang]?.[key] ?? STR.en?.[key] ?? key;
  return s.replace(/\{(\w+)\}/g, (_, k) => (vars[k] ?? "").toString());
}

// Presentation only: formats a figure the API already computed.
function money(v) {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  const dp = Number.isInteger(n) ? 0 : 2;
  return "RM" + n.toLocaleString("en-MY", { minimumFractionDigits: dp, maximumFractionDigits: dp });
}

function monthLabel(m) {
  const d = new Date();
  d.setDate(1);
  d.setMonth(d.getMonth() + Number(m));
  return d.toLocaleDateString(LOCALES[lang], { month: "short", year: "numeric" });
}

const words = (s) => (s || "").replaceAll("_", " ");

// --- auth + API ----------------------------------------------------------------------------------------

let firebaseUser = null;

async function idToken() {
  if (CFG.authMode === "firebase") {
    if (!firebaseUser) {
      const { initializeApp } = await import("https://www.gstatic.com/firebasejs/10.12.2/firebase-app.js");
      const { getAuth, signInAnonymously } = await import("https://www.gstatic.com/firebasejs/10.12.2/firebase-auth.js");
      const auth = getAuth(initializeApp(CFG.firebase));
      firebaseUser = (await signInAnonymously(auth)).user;
    }
    return firebaseUser.getIdToken();
  }
  let uid = localStorage.getItem("lifenest.devUid");
  if (!uid) {
    uid = "web_" + crypto.randomUUID().replaceAll("-", "").slice(0, 16);
    localStorage.setItem("lifenest.devUid", uid);
  }
  return `dev:${uid}`;
}

async function api(method, path, body) {
  const headers = { Authorization: `Bearer ${await idToken()}` };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  let res;
  try {
    res = await fetch(CFG.apiBaseUrl + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch {
    throw Object.assign(new Error(t("offline", { url: CFG.apiBaseUrl })), { status: 0 });
  }
  const data = res.status === 204 ? null : await res.json().catch(() => null);
  if (!res.ok) {
    throw Object.assign(new Error(data?.error?.message || t("error_generic")), { status: res.status, code: data?.error?.code });
  }
  return data;
}

// --- transcript ----------------------------------------------------------------------------------------

function scrollDown() {
  const tr = $("transcript");
  tr.scrollTop = tr.scrollHeight;
}

function addNode(node) {
  $("transcript").append(node);
  scrollDown();
  return node;
}

const addUser = (text) => addNode(h("div", { class: "msg user" }, text));
const addSystem = (text) => addNode(h("div", { class: "msg system" }, h("div", { class: "bubble" }, text)));
const addError = (text) => addNode(h("div", { class: "msg error" }, h("div", { class: "bubble" }, text)));

function setStatus(status) {
  state.profileStatus = status;
  const chip = $("profile-status");
  chip.dataset.status = status;
  chip.textContent = t(`status_${status}`);
}

// --- cards ---------------------------------------------------------------------------------------------

function stat(label, value) {
  return h("div", { class: "stat" }, h("div", { class: "k" }, label), h("div", { class: "v" }, value));
}

function verdictCard(d) {
  let named = null;
  if (d.named_price) {
    const above = Number(d.named_price) > Number(d.max_property_price);
    named = h("div", { class: `named ${above ? "above" : "within"}` },
      h("span", {}, t("verdict_named")), h("span", { class: "price" }, money(d.named_price)),
      h("span", { class: "tag" }, above ? t("verdict_above") : t("verdict_within")));
  }
  return h("div", { class: "card verdict" },
    h("div", { class: "label" }, t("verdict_title")),
    h("div", { class: "hero" }, money(d.max_property_price)),
    named,
    h("div", { class: "stats" },
      d.dsr_pct != null ? stat(t("dsr"), t("dsr_of", { dsr: d.dsr_pct, limit: d.max_dsr_pct })) : null,
      stat(t("instalment"), money(d.monthly_instalment)),
      stat(t("max_loan"), money(d.max_loan)),
      stat(t("tenure"), t("years", { n: d.tenure_years })),
      stat(t("rate"), `${d.effective_rate_pct}%`)),
    h("div", { class: "limit" }, h("strong", {}, `${t("limiting")}: `), t(`lf_${d.limiting_factor}`)),
    h("div", { class: "meta" },
      h("span", {}, t("rules_version", { v: d.rules_version })),
      d.unverified_inputs?.length ? h("span", { class: "chip warn" }, t("unverified", { fields: d.unverified_inputs.join(", ") })) : null));
}

function costsCard(d) {
  const rows = ["down_payment", "stamp_duty_mot", "stamp_duty_loan", "legal_fees", "valuation", "disbursements"]
    .map((k) => h("tr", {}, h("td", {}, t(`cost_${k}`)), h("td", {}, money(d[k]))));
  rows.push(h("tr", { class: "total" }, h("td", {}, t("cost_total_cash_needed")), h("td", {}, money(d.total_cash_needed))));
  rows.push(h("tr", {}, h("td", {}, t("cost_epf_applied")), h("td", {}, money(d.epf_applied))));
  rows.push(h("tr", { class: "short" }, h("td", {}, t("cost_shortfall_after_epf")), h("td", {}, money(d.shortfall_after_epf))));
  if (d.shortfall_after_savings != null) {
    rows.push(h("tr", { class: "short" }, h("td", {}, t("cost_shortfall_after_savings")), h("td", {}, money(d.shortfall_after_savings))));
  }
  return h("div", { class: "card" }, h("h3", {}, t("costs_title", { price: money(d.property_price) })),
    h("table", { class: "costs" }, h("tbody", {}, rows)));
}

function scenarioCard(d) {
  return h("div", { class: "card scenario" },
    h("div", { class: "head" }, h("strong", {}, t(`sc_${d.scenario}`)), h("span", { class: `chip ${d.status}` }, t(`st_${d.status}`))),
    h("p", { class: "muted" }, t("sc_at", { price: money(d.property_price) })),
    d.break_month != null ? h("p", {}, t("sc_breaks", { m: d.break_month })) : null,
    Number(d.monthly_shortfall) > 0 ? h("p", {}, t("sc_shortfall", { amount: money(d.monthly_shortfall) })) : null,
    h("p", { class: "muted small" }, t("sc_buffer", { n: d.buffer_months })),
    h("div", { class: "muted small" }, t("chart_label")),
    balanceChart(d.projection, d.status, d.break_month));
}

function pathCard(d) {
  const items = d.actions.map((a) => h("li", {},
    h("span", { class: "when" }, a.action === "SAVE_UPFRONT_CASH" || a.action === "INCREASE_NET_INCOME"
      ? "" : `${t("month_n", { m: a.by_month })} · ${monthLabel(a.by_month)}`),
    t(`act_${a.action}`, { kind: words(a.commitment_type), amount: money(a.amount), monthly: money(a.monthly_saving) })));
  items.push(h("li", { class: "goal" }, h("strong", {}, d.eligible_from_month != null
    ? t("path_eligible", { m: d.eligible_from_month, date: monthLabel(d.eligible_from_month) })
    : t("path_not_eligible"))));
  return h("div", { class: "card" }, h("h3", {}, t("path_title", { price: money(d.target_property_price) })),
    h("ul", { class: "timeline" }, items));
}

function matchesCard(d) {
  const tiles = d.listings.map((x) => h("div", { class: "listing" },
    h("div", { class: "price" }, money(x.price)),
    h("div", {}, x.title),
    h("div", { class: "muted" }, `${x.area} · ${t("beds", { n: x.bedrooms })} · ${x.sqft} sqft`),
    h("div", { class: "muted small" }, [t(`status_${x.completion_status}`), x.nearest_transit].filter(Boolean).join(" · "))));
  return h("div", { class: "card" }, h("h3", {}, t("matches_title", { price: money(d.max_price) })),
    tiles.length ? h("div", { class: "listings" }, tiles) : h("p", { class: "muted" }, t("matches_none")));
}

function renderCards(cards) {
  const wrap = h("div", { class: "cards" });
  const scenarios = cards.filter((c) => c.type === "scenario_result");
  for (const c of cards) {
    if (c.type === "affordability_verdict") wrap.append(verdictCard(c.data));
    else if (c.type === "cost_breakdown") wrap.append(costsCard(c.data));
    else if (c.type === "path_to_approval") wrap.append(pathCard(c.data));
    else if (c.type === "property_matches") wrap.append(matchesCard(c.data));
  }
  if (scenarios.length) {
    wrap.append(h("div", {}, h("h3", {}, t("scenarios_title")),
      h("div", { class: "scenario-strip" }, scenarios.map((c) => scenarioCard(c.data)))));
  }
  return wrap;
}

function renderSources(sources) {
  const seen = new Map();
  for (const s of sources || []) seen.set(`${s.doc_id}|${s.section}`, s);
  if (!seen.size) return null;
  return h("details", { class: "sources", open: true }, h("summary", {}, t("sources_title")),
    h("ul", {}, [...seen.values()].map((s) => h("li", {}, h("strong", {}, s.title), ` — ${s.section}`))));
}

function renderTrace(trace) {
  if (!trace?.length) return null;
  return h("details", { class: "trace" }, h("summary", {}, t("trace_title", { n: trace.length })),
    trace.map((r) => h("div", { class: `trace-row ${r.status === "ok" ? "" : "error"}` },
      h("code", {}, r.tool), h("span", {}, r.error_code || r.status), h("span", {}, `${r.latency_ms} ms`))));
}

function renderMarkdown(text) {
  const inlinePattern = /(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`|\[[^\]]+\]\(https?:\/\/[^\s)]+\))/g;
  const inline = (value) => {
    const fragment = document.createDocumentFragment();
    let offset = 0;
    for (const match of value.matchAll(inlinePattern)) {
      fragment.append(value.slice(offset, match.index));
      const token = match[0];
      if (token.startsWith("**")) fragment.append(h("strong", {}, token.slice(2, -2)));
      else if (token.startsWith("*")) fragment.append(h("em", {}, token.slice(1, -1)));
      else if (token.startsWith("`")) fragment.append(h("code", {}, token.slice(1, -1)));
      else {
        const [, label, url] = token.match(/^\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)$/);
        fragment.append(h("a", { href: url, target: "_blank", rel: "noopener noreferrer" }, label));
      }
      offset = match.index + token.length;
    }
    fragment.append(value.slice(offset));
    return fragment;
  };

  const root = h("div", { class: "markdown" });
  let paragraph = [];
  let list = null;
  let listTag = null;
  const flushParagraph = () => {
    if (!paragraph.length) return;
    root.append(h("p", {}, paragraph.flatMap((line, i) => i ? [h("br"), inline(line)] : [inline(line)])));
    paragraph = [];
  };
  const flushList = () => {
    if (list) root.append(list);
    list = null;
    listTag = null;
  };
  for (const line of text.split(/\r?\n/)) {
    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    const item = line.match(/^\s*[-*+]\s+(.+)$/);
    const orderedItem = line.match(/^\s*\d+[.)]\s+(.+)$/);
    if (!line.trim() || heading || item || orderedItem || /^\s*---+\s*$/.test(line)) flushParagraph();
    if (!line.trim()) {
      flushList();
    } else if (heading) {
      flushList();
      root.append(h(`h${heading[1].length + 1}`, {}, inline(heading[2])));
    } else if (/^\s*---+\s*$/.test(line)) {
      flushList();
      root.append(h("hr"));
    } else if (item || orderedItem) {
      const tag = orderedItem ? "ol" : "ul";
      const content = (orderedItem || item)[1];
      if (listTag !== tag) {
        flushList();
        list = h(tag, {});
        listTag = tag;
      }
      list.append(h("li", {}, inline(content)));
    } else {
      flushList();
      paragraph.push(line);
    }
  }
  flushParagraph();
  flushList();
  return root;
}

function renderAssistant(resp) {
  return addNode(h("div", { class: "msg assistant" },
    h("div", { class: "bubble" }, renderMarkdown(resp.reply)),
    renderCards(resp.cards || []),
    renderSources(resp.sources),
    renderTrace(resp.tool_trace)));
}

// --- extraction review -----------------------------------------------------------------------------------

function warningHints(warnings) {
  const byPath = {};
  const general = [];
  for (const w of warnings) {
    const [kind, path] = w.split(":");
    if (kind === "kind_mismatch") general.push(t("warn_kind_mismatch", { kind: t(`kind_${path}`) }));
    else (byPath[path] ||= []).push(t(`warn_${kind}`));
  }
  return { byPath, general };
}

function reviewCard(ex) {
  const { byPath, general } = warningHints(ex.warnings || []);
  const inputs = [];
  const rows = ex.fields.map((f) => {
    const hints = byPath[f.path] || [];
    const conf = h("div", { class: "conf" }, `${t("confidence")}: ${Math.round(f.confidence * 100)}%`);
    if (f.path === "commitments") {
      const lines = f.value.map((c, i) => {
        const monthly = h("input", { type: "number", step: "0.01", min: "0", value: c.monthly, "aria-label": t("commitment_monthly") });
        const remaining = h("input", { type: "number", step: "1", min: "0", value: c.remaining_months ?? "", "aria-label": t("commitment_remaining") });
        inputs.push({ path: "commitments", index: i, type: c.type, monthly, remaining, original: c });
        return h("div", { class: "commitment" }, h("span", {}, words(c.type)), monthly, remaining);
      });
      return h("div", { class: `field-row ${hints.length ? "flag" : ""}` },
        h("div", {}, h("div", {}, t("field_commitments")), conf, hints.map((x) => h("div", { class: "hint" }, x))),
        h("div", { class: "muted small" }, `${t("commitment_monthly")} / ${t("commitment_remaining")}`), lines);
    }
    const input = h("input", { type: f.path === "employment.type" ? "text" : "number",
      step: SCALAR_FIELDS.has(f.path) ? "1" : "0.01", value: f.value, "aria-label": t(`field_${f.path}`) });
    inputs.push({ path: f.path, input, original: String(f.value) });
    return h("div", { class: `field-row ${hints.length ? "flag" : ""}` },
      h("div", {}, h("div", {}, t(`field_${f.path}`)), conf, hints.map((x) => h("div", { class: "hint" }, x))), input);
  });
  const savings = h("input", { type: "number", min: "0", step: "0.01", placeholder: "0.00" });
  const confirmBtn = h("button", { type: "button", class: "button" }, t("confirm"));
  const cancelBtn = h("button", { type: "button", class: "button ghost" }, t("cancel"));

  const card = h("div", { class: "card review" },
    h("h3", {}, `${t("review_title")} — ${t(`kind_${ex.kind_detected}`)}`),
    h("p", { class: "muted small" }, t("review_hint")),
    general.map((g) => h("p", { class: "chip warn" }, g)),
    rows,
    h("label", { class: "savings" }, t("savings_label"), savings),
    h("div", { class: "actions" }, cancelBtn, confirmBtn));

  const finish = () => { confirmBtn.disabled = true; cancelBtn.disabled = true; card.querySelectorAll("input").forEach((i) => { i.disabled = true; }); };

  confirmBtn.addEventListener("click", async () => {
    const corrections = {};
    const commitments = inputs.filter((i) => i.path === "commitments");
    for (const i of inputs.filter((x) => x.path !== "commitments")) {
      if (i.input.value !== i.original) corrections[i.path] = i.input.value;
    }
    if (commitments.some((c) => c.monthly.value !== String(c.original.monthly) || c.remaining.value !== String(c.original.remaining_months ?? ""))) {
      corrections.commitments = commitments.map((c) => ({ type: c.type, monthly: c.monthly.value,
        remaining_months: c.remaining.value === "" ? null : Number(c.remaining.value) }));
    }
    if (savings.value) corrections.savings = savings.value;
    confirmBtn.disabled = true;
    try {
      const res = await api("POST", `/actions/${ex.action_id}/confirm`, { corrections });
      finish();
      setStatus(res.profile_status);
      addSystem(res.missing_fields.length
        ? t("confirmed_missing", { fields: res.missing_fields.map((p) => t(`field_${p}`)).join(", ") })
        : t("confirmed_ok"));
    } catch (e) {
      confirmBtn.disabled = false;
      addError(e.message);
    }
  });

  cancelBtn.addEventListener("click", async () => {
    try {
      const res = await api("POST", `/actions/${ex.action_id}/cancel`);
      finish();
      setStatus(res.profile_status);
      addSystem(t("review_cancelled"));
    } catch (e) {
      addError(e.message);
    }
  });
  return addNode(h("div", { class: "msg assistant" }, card));
}

// --- actions ---------------------------------------------------------------------------------------------

async function withBusy(fn) {
  if (state.busy) return;
  state.busy = true;
  try {
    await fn();
  } catch (e) {
    addError(e.message || t("error_generic"));
  } finally {
    state.busy = false;
  }
}

async function send(text) {
  const message = text.trim();
  if (!message) return;
  $("message").value = "";
  addUser(message);
  const thinking = addSystem(t("thinking"));
  await withBusy(async () => {
    try {
      const resp = await api("POST", `/sessions/${state.sessionId}/messages`, { message });
      thinking.remove();
      renderAssistant(resp);
      if (resp.profile_status) setStatus(resp.profile_status);
    } catch (e) {
      thinking.remove();
      throw e;
    }
  });
}

async function uploadDocument(file) {
  const kind = $("doc-kind").value;
  if (!ALLOWED_TYPES.includes(file.type)) {
    addError(t("upload_hint"));
    return;
  }
  const note = addSystem(t("uploading"));
  await withBusy(async () => {
    try {
      const up = await api("POST", `/sessions/${state.sessionId}/documents/upload-url`, { kind, content_type: file.type });
      const put = await fetch(up.upload_url, { method: "PUT", headers: { "Content-Type": file.type }, body: file });
      if (!put.ok) {
        const err = await put.json().catch(() => null);
        throw new Error(err?.error?.message || t("error_generic"));
      }
      const ex = await api("POST", `/sessions/${state.sessionId}/documents/${up.doc_id}/extract`);
      note.remove();
      addSystem(t("uploaded", { kind: t(`kind_${kind}`) }));
      setStatus(ex.profile_status);
      reviewCard(ex);
    } catch (e) {
      note.remove();
      throw e;
    }
  });
}

async function loadDemo() {
  await withBusy(async () => {
    const res = await api("POST", `/sessions/${state.sessionId}/demo-profile`, { demo_id: $("demo-id").value });
    setStatus(res.profile_status);
    addSystem(t("demo_loaded", { name: res.display_name }));
  });
}

async function deleteData() {
  if (!window.confirm(t("delete_confirm"))) return;
  await withBusy(async () => {
    await api("DELETE", `/sessions/${state.sessionId}`);
    sessionStorage.removeItem("lifenest.session");
    $("transcript").replaceChildren();
    await newSession();
    addSystem(t("deleted"));
  });
}

// --- session ---------------------------------------------------------------------------------------------

async function newSession() {
  const s = await api("POST", "/sessions", { locale: lang });
  state.sessionId = s.session_id;
  sessionStorage.setItem("lifenest.session", s.session_id);
  setStatus(s.profile_status);
}

async function restoreSession() {
  const saved = sessionStorage.getItem("lifenest.session");
  if (saved) {
    try {
      const s = await api("GET", `/sessions/${saved}`);
      state.sessionId = s.session_id;
      setStatus(s.profile_status);
      const { messages } = await api("GET", `/sessions/${saved}/messages?limit=50`);
      for (const m of messages) {
        if (m.role === "user") addUser(m.content);
        else renderAssistant({ reply: m.content, cards: m.cards, sources: m.sources, tool_trace: m.tool_trace });
      }
      return;
    } catch (e) {
      if (e.status !== 404) throw e;
    }
  }
  await newSession();
}

// --- i18n ------------------------------------------------------------------------------------------------

function applyLanguage() {
  document.documentElement.lang = lang === "zh" ? "zh-Hans" : lang;
  document.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = t(el.dataset.i18n); });
  document.querySelectorAll("[data-lang]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.lang === lang)));
  $("message").placeholder = t("placeholder");
  setStatus(state.profileStatus);
  const quick = $("quick");
  quick.replaceChildren(...["q1", "q2", "q3", "q4", "q5", "q6"].map((k) =>
    h("button", { type: "button", onclick: () => send(t(k)) }, t(k))));
}

// --- boot ------------------------------------------------------------------------------------------------

async function boot() {
  STR = await (await fetch("i18n.json")).json();
  if (!STR[lang]) lang = "en";
  applyLanguage();

  document.querySelectorAll("[data-lang]").forEach((b) => b.addEventListener("click", () => {
    lang = b.dataset.lang;
    localStorage.setItem("lifenest.lang", lang);
    applyLanguage();
  }));
  $("composer").addEventListener("submit", (e) => { e.preventDefault(); send($("message").value); });
  $("message").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send($("message").value); }
  });
  $("doc-file").addEventListener("change", (e) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (file) uploadDocument(file);
  });
  $("demo-load").addEventListener("click", loadDemo);
  $("delete-data").addEventListener("click", deleteData);

  try {
    const health = await fetch(`${CFG.apiBaseUrl}/health`).then((r) => r.json());
    $("service-info").textContent = `${t("rules_version", { v: health.rules_version })} · LLM: ${health.llm}`;
    await restoreSession();
  } catch (e) {
    addError(e.status === 0 || e instanceof TypeError ? t("offline", { url: CFG.apiBaseUrl }) : e.message);
  }
}

boot();
