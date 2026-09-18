import { loadCatalog } from '$lib/server/data';

export const prerender = true;
const ORIGIN = 'https://scotusbriefs.us';

function encode(value: string): string {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;');
}

export async function GET() {
  const catalog = await loadCatalog();
  const paths = [
    '/',
    '/about/',
    '/search/',
    '/terms/',
    ...catalog.terms.map(({ term }) => `/terms/${encodeURIComponent(term)}/`),
    ...catalog.cases.map(({ slug }) => `/cases/${slug}/`)
  ];
  const body = `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${paths.map((path) => `  <url><loc>${encode(`${ORIGIN}${path}`)}</loc></url>`).join('\n')}\n</urlset>\n`;
  return new Response(body, { headers: { 'content-type': 'application/xml; charset=utf-8' } });
}
