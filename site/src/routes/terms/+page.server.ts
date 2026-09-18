import { loadCatalog } from '$lib/server/data';

export async function load() {
  return loadCatalog();
}
