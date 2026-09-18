import { loadCatalog } from '$lib/server/data';

export async function load() {
  const { terms } = await loadCatalog();
  return { terms };
}
