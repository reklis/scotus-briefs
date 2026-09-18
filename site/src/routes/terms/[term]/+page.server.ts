import { error } from '@sveltejs/kit';
import { compareDockets, loadCatalog } from '$lib/server/data';

export async function entries() {
  const { terms } = await loadCatalog();
  return terms.map(({ term }) => ({ term }));
}

export async function load({ params }) {
  const catalog = await loadCatalog();
  const summary = catalog.terms.find(({ term }) => term === params.term);
  if (!summary) error(404, 'Term not found');
  const cases = catalog.cases
    .filter((item) => item.term === params.term)
    .sort((a, b) => compareDockets(a.dockets[0], b.dockets[0]));
  return { summary, cases, terms: catalog.terms };
}
