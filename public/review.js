const els = {
  title: document.querySelector("#reviewPageTitle"),
  meta: document.querySelector("#reviewMeta"),
  search: document.querySelector("#reviewSearch"),
  tagFilter: document.querySelector("#tagFilter"),
  sortMode: document.querySelector("#sortMode"),
  tagCount: document.querySelector("#tagCount"),
  tagList: document.querySelector("#reviewTagList"),
  visibleCount: document.querySelector("#visibleCount"),
  reviewList: document.querySelector("#reviewList"),
  demoStatus: document.querySelector("#demoScenarioStatus"),
  scenarioList: document.querySelector("#demoScenarioList"),
  addForm: document.querySelector("#addReviewForm"),
  addStatus: document.querySelector("#addReviewStatus"),
  addBody: document.querySelector("#newReviewBody"),
  addRating: document.querySelector("#newReviewRating"),
  addDate: document.querySelector("#newReviewDate"),
  addTags: document.querySelector("#newReviewTags"),
  addButton: document.querySelector("#addReviewButton"),
};

const demoScenarios = [
  {
    title: "Manual Brew Date",
    body:
      "Demo review: the Panama Gesha manual-brew flight was the best thing on the menu today. The slow bar seat was quiet enough for a first date conversation, and the barista explained tasting notes without rushing us. No line at 2:30 PM.",
    rating: 5,
    reviewDate: "2026-05-16",
    tags: "manual-brew hand-pour gesha quiet date no-line demo",
  },
  {
    title: "Oat Matcha",
    body:
      "Demo review: sesame matcha with oat milk tasted nutty and not too sweet. Staff confirmed the drink can be made dairy-free, which helped my lactose-sensitive friend order confidently.",
    rating: 5,
    reviewDate: "2026-05-16",
    tags: "matcha oat-milk dairy-free lactose-sensitive demo",
  },
  {
    title: "Latest Wi-Fi Conflict",
    body:
      "Demo review: Wi-Fi dropped twice during a video call today, even at the back rail. It is still a calm room, but I would not trust it for an important laptop meeting this week.",
    rating: 2,
    reviewDate: "2026-05-16",
    tags: "wifi laptop quiet conflict latest-review demo",
  },
  {
    title: "No-Car Logistics",
    body:
      "Demo review: walking from Muni was easy and bike racks were open, but street parking circled forever after 9:30 AM. I would only drive if the paid garage is acceptable.",
    rating: 4,
    reviewDate: "2026-05-16",
    tags: "transit walking bike parking no-car demo",
  },
];

let allReviews = [];
let allTags = [];

els.search.addEventListener("input", renderReviews);
els.tagFilter.addEventListener("change", renderReviews);
els.sortMode.addEventListener("change", renderReviews);
els.addForm.addEventListener("submit", addReview);
els.reviewList.addEventListener("click", handleReviewListClick);

boot();

async function boot() {
  els.addDate.value = new Date().toISOString().slice(0, 10);
  renderDemoScenarios();
  const payload = await request("/api/reviews");
  applyReviewPayload(payload);
  els.title.textContent = `${payload.business?.name || "Business"} Reviews`;
}

function applyReviewPayload(payload) {
  allReviews = payload.reviews || [];
  allTags = payload.tagCounts || [];
  els.meta.textContent = `${payload.reviewCount || allReviews.length} reviews`;
  renderTagOptions();
  renderTagList();
  renderReviews();
}

function renderDemoScenarios() {
  els.scenarioList.innerHTML = "";
  for (const scenario of demoScenarios) {
    const card = document.createElement("article");
    card.className = "scenario-card";
    card.innerHTML = `
      <div>
        <strong>${escapeHtml(scenario.title)}</strong>
        <p>${escapeHtml(scenario.body)}</p>
        <div class="tag-pills">${scenario.tags
          .split(/\s+/)
          .slice(0, 5)
          .map((item) => `<span>${escapeHtml(item)}</span>`)
          .join("")}</div>
      </div>
      <button class="scenario-add-button" type="button">Add Demo Review</button>
    `;
    card.querySelector(".scenario-add-button").addEventListener("click", () => addScenarioReview(scenario));
    els.scenarioList.append(card);
  }
}

async function addScenarioReview(scenario) {
  setScenarioAdding(true, scenario.title);
  try {
    const payload = await request("/api/reviews", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(scenario),
    });
    applyReviewPayload(payload);
    focusReview(payload.addedReview?.id);
    els.demoStatus.textContent = `Added ${payload.addedReview?.id || "review"}`;
    els.addStatus.textContent = `Added ${payload.addedReview?.id || "review"}`;
  } catch (error) {
    els.demoStatus.textContent = error.message || "Add failed";
  } finally {
    setScenarioAdding(false);
  }
}

async function addReview(event) {
  event.preventDefault();
  const body = els.addBody.value.trim();
  if (!body) {
    els.addStatus.textContent = "Review text required";
    return;
  }
  setAdding(true);
  try {
    const payload = await request("/api/reviews", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        body,
        rating: Number(els.addRating.value),
        reviewDate: els.addDate.value,
        tags: els.addTags.value,
      }),
    });
    applyReviewPayload(payload);
    focusReview(payload.addedReview?.id);
    els.addBody.value = "";
    els.addTags.value = "";
    els.addStatus.textContent = `Added ${payload.addedReview?.id || "review"}`;
  } catch (error) {
    els.addStatus.textContent = error.message || "Add failed";
  } finally {
    setAdding(false);
  }
}

async function handleReviewListClick(event) {
  const button = event.target.closest("[data-delete-review]");
  if (!button) return;
  const reviewId = button.dataset.deleteReview;
  if (!reviewId) return;
  button.disabled = true;
  button.textContent = "Deleting...";
  els.addStatus.textContent = `Deleting ${reviewId}`;
  try {
    const payload = await request(`/api/reviews/${encodeURIComponent(reviewId)}`, {
      method: "DELETE",
    });
    applyReviewPayload(payload);
    els.search.value = "";
    renderReviews();
    els.addStatus.textContent = `Deleted ${payload.deletedReview?.id || reviewId}`;
    els.demoStatus.textContent = `Deleted ${payload.deletedReview?.id || reviewId}`;
  } catch (error) {
    els.addStatus.textContent = error.message || "Delete failed";
    button.disabled = false;
    button.textContent = "Delete";
  }
}

function focusReview(reviewId) {
  els.sortMode.value = "newest";
  els.search.value = reviewId || "";
  els.tagFilter.value = "";
  renderReviews();
}

function renderTagOptions() {
  els.tagFilter.innerHTML = `<option value="">All tags</option>`;
  for (const tag of allTags) {
    const option = document.createElement("option");
    option.value = tag.tag;
    option.textContent = `${tag.tag} (${tag.count})`;
    els.tagFilter.append(option);
  }
}

function renderTagList() {
  els.tagCount.textContent = `${allTags.length} tags`;
  els.tagList.innerHTML = "";
  for (const tag of allTags) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "tag-button";
    button.innerHTML = `<strong>${escapeHtml(tag.tag)}</strong><span>${tag.count} mentions</span>`;
    button.addEventListener("click", () => {
      els.tagFilter.value = tag.tag;
      renderReviews();
    });
    els.tagList.append(button);
  }
}

function renderReviews() {
  const query = els.search.value.trim().toLowerCase();
  const tag = els.tagFilter.value;
  const sortMode = els.sortMode.value;
  let reviews = allReviews.filter((review) => {
    const tagMatch = !tag || (review.tags || []).includes(tag);
    const text = `${review.id} ${review.reviewDate} ${review.rating} ${(review.tags || []).join(" ")} ${review.body}`.toLowerCase();
    const queryMatch = !query || text.includes(query);
    return tagMatch && queryMatch;
  });

  reviews = [...reviews].sort((a, b) => {
    if (sortMode === "rating") return b.rating - a.rating || b.reviewDate.localeCompare(a.reviewDate) || a.id.localeCompare(b.id);
    if (sortMode === "id") return a.id.localeCompare(b.id);
    return b.reviewDate.localeCompare(a.reviewDate) || a.id.localeCompare(b.id);
  });

  els.visibleCount.textContent = `${reviews.length} shown`;
  els.reviewList.innerHTML = "";
  for (const review of reviews) {
    const card = document.createElement("article");
    card.className = "review-row";
    const canDelete = (review.tags || []).includes("user-added");
    card.innerHTML = `
      <div class="review-row-head">
        <strong>${escapeHtml(review.id)}</strong>
        <div class="review-row-meta">
          <span>${escapeHtml(review.reviewDate || "")} · ${review.rating}/5</span>
          ${
            canDelete
              ? `<button class="delete-review-button" type="button" data-delete-review="${escapeHtml(review.id)}">Delete</button>`
              : ""
          }
        </div>
      </div>
      <p>${escapeHtml(review.body)}</p>
      <div class="tag-pills">${(review.tags || []).map((item) => `<span>${escapeHtml(item)}</span>`).join("")}</div>
    `;
    els.reviewList.append(card);
  }
}

function setScenarioAdding(isAdding, title = "") {
  const buttons = els.scenarioList.querySelectorAll("button");
  for (const button of buttons) {
    button.disabled = isAdding;
  }
  if (isAdding) {
    els.demoStatus.textContent = `Adding ${title}`;
  } else if (els.demoStatus.textContent.startsWith("Adding ")) {
    els.demoStatus.textContent = "Ready";
  }
}

function setAdding(isAdding) {
  els.addButton.disabled = isAdding;
  els.addButton.textContent = isAdding ? "Adding..." : "Add Review";
  if (isAdding) {
    els.addStatus.textContent = "Indexing";
  }
}

async function request(url, options = {}) {
  const response = await fetch(url, options);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Request failed");
  return payload;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}
