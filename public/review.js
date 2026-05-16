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
  addForm: document.querySelector("#addReviewForm"),
  addStatus: document.querySelector("#addReviewStatus"),
  addBody: document.querySelector("#newReviewBody"),
  addRating: document.querySelector("#newReviewRating"),
  addDate: document.querySelector("#newReviewDate"),
  addTags: document.querySelector("#newReviewTags"),
  addButton: document.querySelector("#addReviewButton"),
};

let allReviews = [];
let allTags = [];

els.search.addEventListener("input", renderReviews);
els.tagFilter.addEventListener("change", renderReviews);
els.sortMode.addEventListener("change", renderReviews);
els.addForm.addEventListener("submit", addReview);

boot();

async function boot() {
  els.addDate.value = new Date().toISOString().slice(0, 10);
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
    els.sortMode.value = "newest";
    els.search.value = payload.addedReview?.id || "";
    els.tagFilter.value = "";
    renderReviews();
    els.addBody.value = "";
    els.addTags.value = "";
    els.addStatus.textContent = `Added ${payload.addedReview?.id || "review"}`;
  } catch (error) {
    els.addStatus.textContent = error.message || "Add failed";
  } finally {
    setAdding(false);
  }
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
    card.innerHTML = `
      <div class="review-row-head">
        <strong>${escapeHtml(review.id)}</strong>
        <span>${escapeHtml(review.reviewDate || "")} · ${review.rating}/5</span>
      </div>
      <p>${escapeHtml(review.body)}</p>
      <div class="tag-pills">${(review.tags || []).map((item) => `<span>${escapeHtml(item)}</span>`).join("")}</div>
    `;
    els.reviewList.append(card);
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
