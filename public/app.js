const els = {
  defaultWikiOutput: document.querySelector("#defaultWikiOutput"),
  userText: document.querySelector("#userText"),
  generateButton: document.querySelector("#generateButton"),
  wikiOutput: document.querySelector("#wikiOutput"),
  factList: document.querySelector("#factList"),
  evidenceList: document.querySelector("#evidenceList"),
  indexStatus: document.querySelector("#indexStatus"),
  sessionStatus: document.querySelector("#sessionStatus"),
  memoryStatus: document.querySelector("#memoryStatus"),
  generatorStatus: document.querySelector("#generatorStatus"),
  updatedAt: document.querySelector("#updatedAt"),
};

const loadingSteps = ["Parsing user info", "Retrieving reviews", "Synthesizing wiki"];
let loadingTimer = null;

els.generateButton.addEventListener("click", generateWiki);

boot();

async function boot() {
  const state = await request("/api/state");
  applyState(state, { hydrateInput: true });
}

async function generateWiki() {
  setLoading(true);
  const body = { userText: els.userText.value };
  try {
    const [payload] = await Promise.all([post("/api/wiki/generate", body), delay(950)]);
    applyState(payload, { hydrateInput: false });
  } catch (error) {
    els.wikiOutput.innerHTML = `<p>${escapeHtml(error.message || String(error))}</p>`;
  } finally {
    setLoading(false);
  }
}

function applyState(state, options = {}) {
  renderStatus(state);
  if (options.hydrateInput) {
    els.userText.value = state.userText || "";
  }
  renderMarkdown(els.defaultWikiOutput, state.defaultWiki || "");
  renderMarkdown(els.wikiOutput, state.wiki || "");
  renderFacts(state.facts || []);
  renderEvidence(state.evidence || []);
}

function renderStatus(state) {
  const index = state.status?.index || state.indexStatus || {};
  const session = state.status?.sessionMemory || state.sessionMemoryStatus || {};
  const memory = state.status?.memory || state.memoryStatus || {};
  const generator = state.status?.generator || state.generatorStatus || {};
  els.indexStatus.textContent = `Redis: ${index.mode || "unknown"}`;
  els.indexStatus.title = index.detail || "";
  els.sessionStatus.textContent = `Session: ${session.mode || "pending"}`;
  els.sessionStatus.title = session.detail || "";
  els.memoryStatus.textContent = `Cognee: ${memory.mode || "unknown"}`;
  els.memoryStatus.title = memory.detail || "";
  els.generatorStatus.textContent = `Generator: ${generator.mode || "ready"}`;
  els.generatorStatus.title = generator.detail || "";
  els.updatedAt.textContent = state.updatedAt ? `Updated ${state.updatedAt}` : "Ready";
}

function renderMarkdown(target, markdown) {
  const blocks = [];
  const lines = markdown.split(/\r?\n/);
  let list = [];

  function flushList() {
    if (!list.length) return;
    blocks.push(`<ul>${list.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`);
    list = [];
  }

  for (const line of lines) {
    if (!line.trim()) {
      flushList();
      continue;
    }
    if (line.startsWith("# ")) {
      flushList();
      blocks.push(`<h1>${escapeHtml(line.slice(2))}</h1>`);
    } else if (line.startsWith("## ")) {
      flushList();
      blocks.push(`<h2>${escapeHtml(line.slice(3))}</h2>`);
    } else if (line.startsWith("- ")) {
      list.push(line.slice(2));
    } else {
      flushList();
      blocks.push(`<p>${escapeHtml(line)}</p>`);
    }
  }
  flushList();
  target.innerHTML = blocks.join("");
}

function renderFacts(facts) {
  els.factList.innerHTML = "";
  if (!facts.length) {
    els.factList.innerHTML = `<div class="fact"><strong>No personalized facts yet</strong><span>Generate once to parse user info.</span></div>`;
    return;
  }
  for (const fact of facts) {
    const item = document.createElement("article");
    item.className = "fact";
    item.innerHTML = `
      <strong>${escapeHtml(fact.type)}</strong>
      <span>${escapeHtml(fact.text)}</span>
      <span>${escapeHtml((fact.matchedReviewIds || []).slice(0, 4).join(", "))}</span>
    `;
    els.factList.append(item);
  }
}

function renderEvidence(reviews) {
  els.evidenceList.innerHTML = "";
  if (!reviews.length) {
    els.evidenceList.innerHTML = `<div class="review"><strong>No evidence yet</strong><span>Retrieved reviews will appear here.</span></div>`;
    return;
  }
  for (const review of reviews.slice(0, 7)) {
    const item = document.createElement("article");
    item.className = "review";
    const source = review.retrievalSource ? ` · ${review.retrievalSource}` : "";
    item.innerHTML = `
      <strong>${escapeHtml(review.id)} · ${escapeHtml(review.reviewDate || "")} · ${review.rating}/5${escapeHtml(source)}</strong>
      <span>${escapeHtml((review.tags || []).slice(0, 5).join(", "))}</span>
      <p>${escapeHtml(review.body)}</p>
    `;
    els.evidenceList.append(item);
  }
}

function setLoading(isLoading) {
  els.generateButton.disabled = isLoading;
  els.generateButton.textContent = isLoading ? "Generating..." : "Generate Wiki";
  if (!isLoading) {
    clearInterval(loadingTimer);
    loadingTimer = null;
    return;
  }
  let stepIndex = 0;
  renderLoading(stepIndex);
  loadingTimer = setInterval(() => {
    stepIndex = Math.min(stepIndex + 1, loadingSteps.length - 1);
    renderLoading(stepIndex);
  }, 320);
}

function renderLoading(activeIndex) {
  els.wikiOutput.innerHTML = `
    <div class="loading-box">
      ${loadingSteps
        .map((step, index) => `<div class="loading-step ${index === activeIndex ? "active" : ""}">${step}</div>`)
        .join("")}
      <div class="skeleton"></div>
      <div class="skeleton" style="width: 82%"></div>
      <div class="skeleton" style="width: 64%"></div>
    </div>
  `;
}

async function request(url, options = {}) {
  const response = await fetch(url, options);
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || "Request failed");
  }
  return payload;
}

function post(url, body) {
  return request(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}
