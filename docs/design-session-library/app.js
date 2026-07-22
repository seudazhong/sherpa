const rows = [...document.querySelectorAll(".session-row")];
const panels = [...document.querySelectorAll("[data-detail]")];
const filters = [...document.querySelectorAll("[data-filter]")];
const search = document.querySelector("#session-search");
const empty = document.querySelector(".empty-results");
const resultTitle = document.querySelector("#result-title");
const resultCount = document.querySelector("#result-count");

let activeFilter = "all";

function selectRow(row) {
  rows.forEach((item) => item.classList.toggle("active", item === row));
  panels.forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.detail === row.dataset.panel);
  });
}

function applyFilters() {
  const query = search.value.trim().toLowerCase();
  let visible = 0;

  rows.forEach((row) => {
    const statusMatches = activeFilter === "all" || row.dataset.status === activeFilter;
    const queryMatches = !query || row.dataset.search.includes(query);
    const show = statusMatches && queryMatches;
    row.hidden = !show;
    if (show) visible += 1;
  });

  empty.hidden = visible !== 0;
  resultTitle.textContent = query ? "Search results" : "Recent sessions";
  resultCount.textContent =
    visible === 1 ? "1 session" : `${visible} sessions${query ? " · grouped matches" : ""}`;

  const selected = rows.find((row) => row.classList.contains("active") && !row.hidden);
  if (!selected) {
    const firstVisible = rows.find((row) => !row.hidden);
    if (firstVisible) selectRow(firstVisible);
  }
}

rows.forEach((row) => row.addEventListener("click", () => selectRow(row)));

filters.forEach((filter) => {
  filter.addEventListener("click", () => {
    activeFilter = filter.dataset.filter;
    filters.forEach((item) => item.classList.toggle("active", item === filter));
    applyFilters();
  });
});

search.addEventListener("input", applyFilters);
applyFilters();
