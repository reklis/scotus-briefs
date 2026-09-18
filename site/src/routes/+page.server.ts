import { loadCatalog } from '$lib/server/data';

export async function load() {
  const catalog = await loadCatalog();
  const latest = catalog.terms.find((term) => term.latest);
  const latestCases = latest ? catalog.cases.filter((item) => item.term === latest.term) : [];
  const recentCases = [...catalog.cases]
    .filter((item) => item.updatedAt || item.guide?.generatedAt)
    .sort((a, b) =>
      (b.updatedAt ?? b.guide?.generatedAt ?? '').localeCompare(
        a.updatedAt ?? a.guide?.generatedAt ?? ''
      )
    )
    .slice(0, 4);
  return {
    latest,
    latestCases: latestCases.slice(0, 4),
    recentCases,
    totalCases: catalog.cases.length
  };
}
