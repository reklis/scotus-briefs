import { error } from '@sveltejs/kit';
import { loadCatalog } from '$lib/server/data';

export async function entries() {
  const { cases } = await loadCatalog();
  return cases.map(({ slug }) => ({ slug }));
}

export async function load({ params }) {
  const { cases } = await loadCatalog();
  const item = cases.find(({ slug }) => slug === params.slug);
  if (!item) error(404, 'Case not found');
  return { item };
}
