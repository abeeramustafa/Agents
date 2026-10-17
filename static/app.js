// The page redraws from the session JSON. It never calls the model itself.
const EXAMPLES = [
  "Electronics under $30 retail, good margins, low returns",
  "I want to start selling something but have no idea what",
  "drones",
];

const STAGES = [
  ["BRIEF", "Brief"],
  ["PRODUCT_REVIEW", "Products"],
  ["SUPPLIER_REVIEW", "Suppliers"],
  ["FINAL_REVIEW", "Plan"],
];

const TREND = { rising: "↑ Rising", stable: "→ Stable", declining: "↓ Declining" };

const main = document.getElementById("main");
const stepper = document.getElementById("stepper");
const agentBody = document.getElementById("agent-body");
const footer = document.getElementById("footer");
const approvedCount = document.getElementById("approved-count");
const continueButton = document.getElementById("continue");
const backButton = document.getElementById("back-stage");
const errorBox = document.getElementById("error");
const composer = document.getElementById("composer");
const replyInput = document.getElementById("reply");

let session = null;
let catalog = null;
let busy = false;
let tab = "chat";
let lastRequest = null;
let workingText = "";
let polling = false;

document.getElementById("tab-chat").addEventListener("click", () => setTab("chat"));
document.getElementById("tab-log").addEventListener("click", () => setTab("log"));
composer.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = replyInput.value.trim();
  if (!text) return;
  replyInput.value = "";
  sendMessage(text);
});
continueButton.addEventListener("click", continueSuppliers);
backButton.addEventListener("click", () => goTo("SUPPLIER_REVIEW"));

boot();

async function boot() {
  try {
    catalog = await api("/api/catalog/meta");
    const saved = sessionStorage.getItem("sourcedesk.session");
    if (saved) {
      try {
        session = await api("/api/session/" + saved);
      } catch (_error) {
        session = null;
      }
    }
    if (!session) {
      session = await api("/api/session", { method: "POST" });
      sessionStorage.setItem("sourcedesk.session", session.id);
    }
    render();
  } catch (error) {
    showError(error, false);
  }
}

async function sendMessage(text) {
  if (busy || !session) return;
  lastRequest = () => sendMessage(text);
  setBusy(true, "Maya is reading your brief…");
  const timer = window.setTimeout(() => setWorking("Omar is ranking products…"), 2000);
  try {
    session = await api("/api/message", {
      method: "POST",
      body: JSON.stringify({ session_id: session.id, text }),
    });
    clearError();
    render();
  } catch (error) {
    showError(error, true);
  } finally {
    window.clearTimeout(timer);
    setBusy(false);
  }
}

async function decide(productId, decision) {
  if (busy || !session) return;
  lastRequest = () => decide(productId, decision);
  setBusy(true, "Saving your decision…");
  try {
    session = await api("/api/products/decision", {
      method: "POST",
      body: JSON.stringify({ session_id: session.id, product_id: productId, decision }),
    });
    clearError();
    render();
  } catch (error) {
    showError(error, true);
  } finally {
    setBusy(false);
  }
}

async function goTo(stage) {
  if (busy || !session) return;
  if (stage === session.stage) return;
  setBusy(true, "Opening that stage…");
  try {
    session = await api("/api/stage/back", {
      method: "POST",
      body: JSON.stringify({ session_id: session.id, stage }),
    });
    clearError();
    render();
  } catch (error) {
    showError(error, false);
  } finally {
    setBusy(false);
  }
}

async function continueSuppliers() {
  if (!session) return;
  if (session.stage === "SUPPLIER_REVIEW") {
    await continueFinal();
    return;
  }
  if (busy) return;
  setBusy(true, "Lina is comparing suppliers…");
  try {
    session = await api("/api/stage/suppliers", {
      method: "POST",
      body: JSON.stringify({ session_id: session.id }),
    });
    clearError();
    render();
    maybePoll();
  } catch (error) {
    showError(error, false);
  } finally {
    setBusy(false);
  }
}

async function continueFinal() {
  if (busy || !session) return;
  setBusy(true, "Sana is writing the plan…");
  try {
    session = await api("/api/stage/final", {
      method: "POST",
      body: JSON.stringify({ session_id: session.id }),
    });
    clearError();
    render();
  } catch (error) {
    showError(error, false);
  } finally {
    setBusy(false);
  }
}

async function retrySupplier(productId) {
  if (busy || !session) return;
  setBusy(true, "Lina is retrying this product…");
  try {
    session = await api("/api/suppliers/retry", {
      method: "POST",
      body: JSON.stringify({ session_id: session.id, product_id: productId }),
    });
    clearError();
    render();
  } catch (error) {
    showError(error, false);
  } finally {
    setBusy(false);
  }
}

async function selectSuppliers(productId, supplierIds) {
  if (!session) return;
  try {
    session = await api("/api/suppliers/select", {
      method: "POST",
      body: JSON.stringify({
        session_id: session.id,
        product_id: productId,
        supplier_ids: supplierIds,
      }),
    });
    clearError();
    render();
  } catch (error) {
    showError(error, false);
  }
}

function maybePoll() {
  if (polling || !session || !session.supplier_progress || !session.supplier_progress.running) return;
  polling = true;
  pollSuppliers();
}

async function pollSuppliers() {
  while (session && session.supplier_progress && session.supplier_progress.running) {
    await new Promise((resolve) => window.setTimeout(resolve, 1200));
    try {
      session = await api("/api/session/" + session.id);
      render();
    } catch (error) {
      showError(error, false);
      break;
    }
  }
  polling = false;
}

async function api(path, options) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const body = await response.json();
  if (!response.ok) {
    const error = new Error(body.error || "Request failed");
    error.hint = body.hint || "";
    throw error;
  }
  return body;
}

function render() {
  renderStepper();
  renderMain();
  renderAgent();
  renderFooter();
  setDisabled(busy);
  maybePoll();
}

function renderStepper() {
  stepper.replaceChildren();
  const reachable = new Set((session && session.reachable_stages) || ["BRIEF"]);
  const current = session ? session.stage : "BRIEF";
  const currentIndex = STAGES.findIndex(([id]) => id === current);
  STAGES.forEach(([id, label], index) => {
    const button = el("button", "", label);
    button.type = "button";
    const canOpen = reachable.has(id);
    if (id === current) {
      button.setAttribute("aria-current", "step");
      button.classList.add("is-current");
    } else if (canOpen && index < currentIndex) {
      button.classList.add("is-done");
    }
    if (!canOpen) {
      button.disabled = true;
      button.classList.add("is-locked");
    } else {
      button.addEventListener("click", () => goTo(id));
    }
    stepper.append(button);
  });
}

function renderMain() {
  main.replaceChildren();
  if (workingText) main.append(el("p", "working", workingText));
  if (!session) return;
  if (busy && session.stage !== "PRODUCT_REVIEW") {
    main.append(el("div", "skeleton"), el("div", "skeleton"), el("div", "skeleton"));
  }
  if (session.stage === "PRODUCT_REVIEW") {
    renderCards();
    return;
  }
  if (session.stage === "SUPPLIER_REVIEW") {
    renderSuppliers();
    return;
  }
  if (session.stage === "FINAL_REVIEW") {
    renderPlan();
    return;
  }
  if (session.gate_status === "out_of_catalog") {
    main.append(outCard());
  }
  main.append(briefPanel());
}

function briefPanel() {
  const panel = el("div", "panel brief");
  panel.append(el("h1", "", "What do you want to sell?"));
  const categories = (catalog && catalog.categories) || session.categories || [];
  panel.append(el("p", "hint", "Categories: " + categories.join(", ") + "."));
  const box = document.createElement("textarea");
  box.id = "brief";
  box.setAttribute("aria-label", "Product brief");
  panel.append(box);
  const examples = el("div", "examples");
  for (const prompt of EXAMPLES) {
    const button = el("button", "chip", prompt);
    button.type = "button";
    button.addEventListener("click", () => sendMessage(prompt));
    examples.append(button);
  }
  panel.append(examples);
  const submit = el("button", "primary", "Find products");
  submit.type = "button";
  submit.style.marginTop = "0.8rem";
  submit.addEventListener("click", () => sendMessage(box.value));
  panel.append(submit);
  if (session.gate_status === "clarify" && session.chips.length) {
    panel.append(el("p", "hint", "Quick replies"));
    panel.append(chips(session.chips));
  }
  return panel;
}

function outCard() {
  const panel = el("div", "panel");
  panel.append(el("h2", "", session.banner || "That item is not in the sample catalog."));
  if (session.note) panel.append(el("p", "muted", session.note));
  const row = el("div", "chips");
  for (const match of session.closest_matches || []) {
    const button = el("button", "chip", match.label);
    button.type = "button";
    button.addEventListener("click", () => sendMessage(match.label));
    row.append(button);
  }
  panel.append(row);
  return panel;
}

function chips(labels) {
  const row = el("div", "chips");
  for (const label of labels) {
    const button = el("button", "chip", label);
    button.type = "button";
    button.addEventListener("click", () => sendMessage(label));
    row.append(button);
  }
  return row;
}

function renderCards() {
  if (session.banner) main.append(el("p", "working", session.banner));
  if (!session.cards.length) {
    main.append(el("p", "muted", "No products to review. Try a broader brief."));
    return;
  }
  for (const card of session.cards) main.append(productCard(card));
}

function productCard(card) {
  const article = el("article", "card");
  if (card.decision) article.classList.add(card.decision);
  article.append(el("p", "meta", card.category + " · " + card.subcategory));
  article.append(el("h2", "", card.name));
  const prices = el("div", "prices");
  prices.append(price("Retail", money(card.retail_price_usd)));
  prices.append(price("Wholesale", money(card.market_wholesale_price_usd)));
  prices.append(price("Est. net margin", card.est_net_margin_pct + "%"));
  article.append(prices);
  article.append(bars(card));
  article.append(el("p", "", TREND[card.trend] || card.trend));
  if (card.risk_flags && card.risk_flags.length) {
    const flags = el("div", "flags");
    for (const flag of card.risk_flags) {
      const label = (flag.code || "").replaceAll("_", " ") || flag.reason || "risk";
      const badge = el("span", "flag", label);
      badge.title = flag.reason || label;
      flags.append(badge);
    }
    article.append(flags);
  }
  if (card.rationale_source === "auto") article.append(el("p", "meta", "Auto summary"));
  if (card.rationale) article.append(el("p", "", card.rationale));
  if (card.watch_out) article.append(el("p", "muted", card.watch_out));
  article.append(breakdown(card));
  const actions = el("div", "actions");
  if (card.decision !== "approved") {
    const approve = el("button", "primary", "Approve");
    approve.type = "button";
    approve.addEventListener("click", () => decide(card.product_id, "approved"));
    actions.append(approve);
  }
  if (card.decision !== "rejected") {
    const reject = el("button", "ghost", "Reject");
    reject.type = "button";
    reject.addEventListener("click", () => decide(card.product_id, "rejected"));
    actions.append(reject);
  } else {
    const undo = el("button", "ghost", "Undo");
    undo.type = "button";
    undo.addEventListener("click", () => decide(card.product_id, "undo"));
    actions.append(undo);
  }
  article.append(actions);
  return article;
}

function price(label, value) {
  const block = el("div", "");
  block.append(el("span", "meta", label));
  block.append(el("strong", "", value));
  return block;
}

function bars(card) {
  const wrap = el("div", "bars");
  wrap.append(bar("Demand", card.demand_score));
  wrap.append(bar("Competition", card.competition_pressure));
  return wrap;
}

function bar(label, value) {
  const row = el("div", "bar");
  row.append(el("span", "", label));
  const track = el("div", "track");
  const fill = el("div", "fill");
  const width = Math.max(0, Math.min(100, Number(value) || 0));
  fill.style.width = width + "%";
  track.append(fill);
  row.append(track);
  row.append(el("span", "", String(Math.round(width))));
  return row;
}

function breakdown(card) {
  const details = document.createElement("details");
  details.append(el("summary", "", "Why this score?"));
  const lines = [
    "Net margin " + card.est_net_margin_pct + "% after a " + Math.round(card.cost_allowance * 100) + "% cost allowance.",
    "Demand " + card.demand_score + " from search " + round1(card.search_index) + ", reviews " + round1(card.reviews_scaled) + ", rating " + round1(card.rating_scaled) + ", trend " + card.trend_bonus + ".",
    "Competition pressure " + card.competition_pressure + ".",
    "Opportunity " + card.opportunity_score + " = demand " + round1(card.demand_points) + " + margin " + round1(card.margin_points) + " + open market " + round1(card.competition_points) + " − risk " + card.risk_penalty + ".",
  ];
  for (const line of lines) details.append(el("p", "muted", line));
  return details;
}

function renderAgent() {
  agentBody.replaceChildren();
  if (!session) return;
  if (tab === "log") {
    for (const event of session.activity_log) {
      const item = el("p", "log-item");
      item.append(el("span", "", formatTime(event.timestamp) + " · " + event.actor));
      item.append(document.createTextNode(withoutIds(event.text || (event.action + " " + event.detail))));
      agentBody.append(item);
    }
    return;
  }
  for (const message of session.chat) {
    const bubble = el("div", "bubble");
    const who = message.name === "You" ? "You" : message.name + " · " + message.role;
    bubble.append(el("span", "who", who));
    bubble.append(el("p", "", withoutIds(message.text)));
    agentBody.append(bubble);
  }
  agentBody.scrollTop = agentBody.scrollHeight;
}

function renderPlan() {
  const plan = session.plan;
  if (!plan) {
    main.append(el("p", "", "No plan yet. Go back and continue from the supplier stage."));
    return;
  }
  main.append(el("h1", "", withoutIds(plan.headline)));
  for (const item of plan.plan || []) {
    const card = el("article", "card");
    card.append(el("h2", "", item.name));
    if (item.limited_supply) card.append(el("p", "badge limited", "Limited supply"));
    const picks = (item.suppliers || []).map((supplier) => {
      return supplier.supplier_name + " · " + money(supplier.unit_price_usd) + " · score " + supplier.supplier_score;
    });
    card.append(el("p", "meta", picks.join(" · ") || "No supplier"));
    card.append(el("p", "", withoutIds(item.why || "")));
    main.append(card);
  }
  main.append(el("h2", "", "Risks to check"));
  main.append(bulletList("checklist", plan.risks_to_check));
  main.append(el("h2", "", "Next steps"));
  main.append(bulletList("steps", plan.next_steps));
  main.append(el("h2", "", "Activity log"));
  const log = el("div", "card");
  for (const event of session.activity_log || []) {
    const item = el("p", "log-item");
    item.append(el("span", "", formatTime(event.timestamp) + " · " + event.actor));
    item.append(document.createTextNode(withoutIds(event.text || (event.action + " " + event.detail))));
    log.append(item);
  }
  main.append(log);
}

function bulletList(className, lines) {
  const list = document.createElement("ul");
  list.className = className;
  for (const line of lines || []) list.append(el("li", "", withoutIds(line)));
  if (!list.childElementCount) list.append(el("li", "", "None"));
  return list;
}

function renderFooter() {
  const products = session && session.stage === "PRODUCT_REVIEW";
  const suppliers = session && session.stage === "SUPPLIER_REVIEW";
  const plan = session && session.stage === "FINAL_REVIEW";
  footer.hidden = !products && !suppliers && !plan;
  backButton.hidden = !plan;
  if (plan) {
    approvedCount.textContent = "Sana wrote this from your picks";
    continueButton.textContent = "Draft outreach (coming soon)";
    continueButton.disabled = true;
    return;
  }
  if (products) {
    const approved = approvedOnScreen();
    approvedCount.textContent = approved + " approved";
    continueButton.textContent = "Continue to suppliers";
    continueButton.disabled = busy || approved < 1;
    return;
  }
  if (!suppliers) return;
  const sections = session.supplier_sections || [];
  const picked = sections.filter((section) => (section.selected_supplier_ids || []).length > 0).length;
  approvedCount.textContent = picked + " of " + sections.length + " products have a supplier";
  continueButton.textContent = "Continue to final plan";
  const ready = sections.length > 0 && sections.every((section) => {
    return !section.offers.length || (section.selected_supplier_ids || []).length > 0;
  });
  continueButton.disabled = busy || !ready;
}

function renderSuppliers() {
  const progress = session.supplier_progress;
  if (progress && progress.running) {
    let text = "Lina is comparing suppliers… " + progress.completed + " of " + progress.total;
    if (progress.total > 6) text += " · batch " + progress.batch + " of " + progress.batches;
    main.append(el("p", "working", text));
  }
  for (const section of session.supplier_sections || []) main.append(supplierSection(section));
}

function supplierSection(section) {
  const article = el("article", "card");
  const title = el("h2", "", section.name);
  article.append(el("p", "meta", section.category));
  article.append(title);
  if (section.limited_supply) {
    article.append(el("p", "badge limited", "Limited supply"));
  }
  if (section.status === "pending") {
    article.append(el("p", "muted", "Lina is comparing this product…"));
  }
  if (section.status === "error") {
    article.append(el("p", "warn", section.error || "Lina could not compare this product."));
    const retry = el("button", "ghost", "Retry");
    retry.type = "button";
    retry.addEventListener("click", () => retrySupplier(section.product_id));
    article.append(retry);
  }
  article.append(offerTable(section));
  if (section.cheapest_supplier_warning) {
    article.append(el("p", "warn", withoutIds(section.cheapest_supplier_warning)));
  }
  if (!section.limited_supply && section.comparison_notes) {
    article.append(el("p", "", withoutIds(section.comparison_notes)));
  }
  if (section.limited_supply && section.comparison_notes) {
    article.append(el("p", "muted", withoutIds(section.comparison_notes)));
  }
  if (section.risks && section.risks.length) {
    for (const risk of section.risks) {
      article.append(el("p", "muted", offerName(section, risk.supplier_id) + ": " + withoutIds(risk.risk)));
    }
  }
  article.append(auditLine(section.audit));
  const note = section.offers[0] && section.offers[0].landed_cost_note;
  if (note) article.append(el("p", "muted", note));
  return article;
}

function offerTable(section) {
  const wrap = el("div", "table-wrap");
  const table = document.createElement("table");
  const head = document.createElement("tr");
  for (const label of ["Pick", "Supplier", "Unit price", "MOQ", "Lead time", "Reliability", "Certifications", "Payment", "Score", "Risks"]) {
    head.append(el("th", "", label));
  }
  table.append(head);
  const selected = new Set(section.selected_supplier_ids || []);
  const recommended = new Set(section.recommended_supplier_ids || []);
  for (const offer of section.offers) {
    const row = document.createElement("tr");
    if (recommended.has(offer.supplier_id)) row.className = "recommended";
    const pick = document.createElement("td");
    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = selected.has(offer.supplier_id);
    box.setAttribute("aria-label", "Select " + offer.supplier_name);
    box.addEventListener("change", () => {
      const next = new Set(section.selected_supplier_ids || []);
      if (box.checked) next.add(offer.supplier_id);
      else next.delete(offer.supplier_id);
      selectSuppliers(section.product_id, [...next]);
    });
    pick.append(box);
    row.append(pick);
    const supplier = el("td", "", offer.supplier_name);
    if (recommended.has(offer.supplier_id)) supplier.append(el("span", "badge", " Lina"));
    row.append(supplier);
    row.append(el("td", "", money(offer.unit_price_usd)));
    row.append(el("td", "", String(offer.min_order_qty)));
    row.append(el("td", "", offer.lead_time_days + " days"));
    row.append(el("td", "", String(offer.reliability_score)));
    row.append(el("td", "", offer.certifications));
    row.append(el("td", "", offer.payment_terms));
    row.append(el("td", "", String(offer.supplier_score)));
    const risks = document.createElement("td");
    for (const flag of offer.risk_flags || []) {
      const badge = el("span", "flag", flag.code.replaceAll("_", " "));
      badge.title = flag.reason;
      risks.append(badge);
    }
    row.append(risks);
    table.append(row);
  }
  wrap.append(table);
  return wrap;
}

function auditLine(audit) {
  if (!audit) return el("p", "muted", "");
  if (audit.skipped) return el("p", "muted", "Audit skipped");
  if (audit.passed) return el("p", "audit", "Audit passed");
  const count = audit.notes || (audit.issues || []).length || 1;
  const line = el("p", "warn", "Karim flagged " + count + " note" + (count === 1 ? "" : "s"));
  for (const issue of audit.issues || []) {
    line.append(el("span", "muted", " " + issue.where + ": " + issue.problem));
  }
  return line;
}

function setTab(next) {
  tab = next;
  document.getElementById("tab-chat").setAttribute("aria-selected", String(next === "chat"));
  document.getElementById("tab-log").setAttribute("aria-selected", String(next === "log"));
  renderAgent();
}

function setBusy(next, message) {
  busy = next;
  workingText = next ? message : "";
  setDisabled(next);
  if (session) render();
}

function setWorking(message) {
  if (!busy) return;
  workingText = message;
  const node = main.querySelector(".working");
  if (node) node.textContent = message;
}

function setDisabled(next) {
  for (const button of document.querySelectorAll("button")) {
    if (button.id === "continue") continue;
    button.disabled = next;
  }
  replyInput.disabled = next;
  const brief = document.getElementById("brief");
  if (brief) brief.disabled = next;
  if (session) renderFooter();
}

function showError(error, canRetry) {
  errorBox.hidden = false;
  errorBox.replaceChildren(document.createTextNode((error.message || "Request failed") + (error.hint ? " " + error.hint : "")));
  if (canRetry && lastRequest) {
    const button = el("button", "ghost", "Retry");
    button.type = "button";
    button.addEventListener("click", () => lastRequest());
    errorBox.append(button);
  }
}

function clearError() {
  errorBox.hidden = true;
  errorBox.replaceChildren();
}

function approvedOnScreen() {
  const fromCards = (session.cards || []).filter((card) => card.decision === "approved").length;
  return Math.max(fromCards, session.approved_count || 0);
}

function formatTime(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso || "";
  return date.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

function withoutIds(text) {
  return String(text || "").replace(/\bP\d{3}\b|\bS\d{2}\b/g, "").replace(/\s{2,}/g, " ").replace(/\s+([,.;:])/g, "$1").trim();
}

function offerName(section, supplierId) {
  const offer = (section.offers || []).find((row) => row.supplier_id === supplierId);
  return offer ? offer.supplier_name : "";
}

function money(value) {
  return "$" + Number(value).toFixed(2);
}

function round1(value) {
  return Math.round(Number(value) * 10) / 10;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}
