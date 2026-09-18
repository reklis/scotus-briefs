import { json } from '@sveltejs/kit';
import { loadCatalog } from '$lib/server/data';

export const prerender = true;

export async function GET() {
  const { searchIndex } = await loadCatalog();
  return json(searchIndex, {
    headers: { 'cache-control': 'public, max-age=3600', 'x-content-type-options': 'nosniff' }
  });
}
