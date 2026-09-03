"use strict";

(function (root) {
  const PAGE_SIZE = 20;
  function normalize(value) { return String(value || "").trim().replace(/\s+/g, " ").toLocaleLowerCase("en-US"); }
  function searchable(item) { return normalize([item.title, item.caption, item.docket, item.term, item.argument_date, item.status].concat(item.topics).join(" ")); }
  function filterCases(cases, query, status, topic) {
    const needle = normalize(query), wantedStatus = normalize(status), wantedTopic = normalize(topic);
    return cases.filter(function (item) {
      return (!needle || searchable(item).includes(needle)) &&
        (!wantedStatus || normalize(item.status) === wantedStatus) &&
        (!wantedTopic || item.topics.some(function (value) { return normalize(value) === wantedTopic; }));
    });
  }
  function pageCases(cases, page) {
    const pages = Math.max(1, Math.ceil(cases.length / PAGE_SIZE));
    const selected = Math.min(Math.max(1, Number(page) || 1), pages);
    return {items: cases.slice((selected - 1) * PAGE_SIZE, selected * PAGE_SIZE), page: selected, pages: pages};
  }
  const api = {normalize: normalize, filterCases: filterCases, pageCases: pageCases, PAGE_SIZE: PAGE_SIZE};
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof document === "undefined") return;

  const script = document.currentScript;
  const form = document.getElementById("case-search");
  if (!script || !form) return;
  const query = document.getElementById("query"), status = document.getElementById("status-filter"), topic = document.getElementById("topic-filter");
  const results = document.getElementById("search-results"), message = document.getElementById("search-status"), pagination = document.getElementById("search-pagination");
  const previous = document.getElementById("search-previous"), next = document.getElementById("search-next"), pageLabel = document.getElementById("search-page");
  let cases = [], currentPage = 1;

  function appendText(parent, tag, value, className) {
    const element = document.createElement(tag); element.textContent = value;
    if (className) element.className = className; parent.appendChild(element); return element;
  }
  function render() {
    const matches = filterCases(cases, query.value, status.value, topic.value);
    const selected = pageCases(matches, currentPage); currentPage = selected.page;
    results.replaceChildren();
    selected.items.forEach(function (item) {
      const li = document.createElement("li"), article = document.createElement("article"), heading = document.createElement("h2"), link = document.createElement("a");
      link.href = item.path; link.textContent = item.title; heading.appendChild(link); article.appendChild(heading);
      appendText(article, "p", item.caption + " · Docket " + item.docket, "eyebrow");
      appendText(article, "p", "October Term " + item.term + " · " + item.status.replace(/_/g, " ") + (item.topics.length ? " · " + item.topics.join(", ") : ""));
      li.appendChild(article); results.appendChild(li);
    });
    message.textContent = matches.length ? "Showing " + (((selected.page - 1) * PAGE_SIZE) + 1) + "–" + Math.min(selected.page * PAGE_SIZE, matches.length) + " of " + matches.length + " results." : "No public case briefs match this search.";
    pagination.hidden = selected.pages <= 1; previous.disabled = selected.page <= 1; next.disabled = selected.page >= selected.pages; pageLabel.textContent = "Page " + selected.page + " of " + selected.pages;
  }
  form.addEventListener("submit", function (event) { event.preventDefault(); currentPage = 1; render(); });
  status.addEventListener("change", function () { currentPage = 1; render(); }); topic.addEventListener("change", function () { currentPage = 1; render(); });
  previous.addEventListener("click", function () { currentPage -= 1; render(); }); next.addEventListener("click", function () { currentPage += 1; render(); });
  fetch(script.dataset.index, {credentials: "same-origin"}).then(function (response) { if (!response.ok) throw new Error("search index unavailable"); return response.json(); }).then(function (payload) {
    cases = payload.cases; const parameters = new URLSearchParams(window.location.search); query.value = parameters.get("q") || ""; status.value = parameters.get("status") || ""; topic.value = parameters.get("topic") || ""; render();
  }).catch(function () { message.textContent = "Search is temporarily unavailable. Browse the generated archives instead."; });
})(typeof globalThis !== "undefined" ? globalThis : this);
