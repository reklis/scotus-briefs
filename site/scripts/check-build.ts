import { readdir, readFile, stat } from 'node:fs/promises';
import { extname, join, relative, resolve } from 'node:path';

const root = resolve('build');
// The recovered historical catalog intentionally prerenders thousands of lightweight case
// pages. Keep a firm ceiling while leaving room for resumable guide backfill growth.
const maxBytes = Number(process.env.PAGES_ARTIFACT_MAX_BYTES ?? 128 * 1024 * 1024);

async function filesBelow(directory: string): Promise<string[]> {
  const entries = await readdir(directory, { withFileTypes: true });
  return (
    await Promise.all(
      entries.map((entry) => {
        const path = join(directory, entry.name);
        return entry.isDirectory() ? filesBelow(path) : [path];
      })
    )
  ).flat();
}

const files = await filesBelow(root);
const relativeFiles = files.map((file) => relative(root, file).replaceAll('\\', '/'));
const required = [
  'index.html',
  'terms/index.html',
  'search/index.html',
  'about/index.html',
  'robots.txt',
  'sitemap.xml',
  'search-index.json',
  'CNAME',
  '404.html'
];
const missing = required.filter((file) => !relativeFiles.includes(file));
if (missing.length) throw new Error(`Static build is missing: ${missing.join(', ')}`);
const pdfs = relativeFiles.filter(
  (file) => extname(file).toLowerCase() === '.pdf' || file.startsWith('documents/')
);
if (pdfs.length) throw new Error(`PDF archive leaked into Pages output: ${pdfs.join(', ')}`);
const sizes = await Promise.all(files.map(async (file) => (await stat(file)).size));
const bytes = sizes.reduce((total, size) => total + size, 0);
if (bytes > maxBytes) throw new Error(`Pages output is ${bytes} bytes; limit is ${maxBytes} bytes`);

const outputPaths = new Set(relativeFiles);
const broken: string[] = [];
for (const file of files.filter((path) => path.endsWith('.html'))) {
  const html = await readFile(file, 'utf8');
  for (const match of html.matchAll(/href=["']([^"']+)["']/g)) {
    const href = match[1];
    if (!href.startsWith('/') || href.startsWith('//')) continue;
    const pathname = decodeURI(href.split(/[?#]/)[0]);
    if (!pathname) continue;
    const target = pathname.endsWith('/') ? `${pathname.slice(1)}index.html` : pathname.slice(1);
    if (!outputPaths.has(target) && !outputPaths.has(`${target}/index.html`)) {
      broken.push(`${relative(root, file)} -> ${href}`);
    }
  }
  if (
    !/<main[\s>]/.test(html) ||
    !/<title>[^<]+<\/title>/.test(html) ||
    !/rel="canonical"/.test(html)
  ) {
    broken.push(`${relative(root, file)} -> missing landmark or metadata`);
  }
}
if (broken.length) throw new Error(`Broken static output:\n${broken.join('\n')}`);
console.log(
  `Validated ${files.length} files (${bytes} bytes): static routes, links, metadata, size, and PDF exclusion.`
);
