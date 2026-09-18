import { execFileSync } from 'node:child_process';
import { readFile, readdir } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import axe from 'axe-core';
import { JSDOM } from 'jsdom';
import { describe, expect, it } from 'vitest';

const build = resolve('build');

async function filesBelow(directory: string): Promise<string[]> {
  return (
    await Promise.all(
      (await readdir(directory, { withFileTypes: true })).map((entry) => {
        const path = join(directory, entry.name);
        return entry.isDirectory() ? filesBelow(path) : [path];
      })
    )
  ).flat();
}

describe('prerendered publication', () => {
  it('emits all static routes, metadata files, and no PDF bytes', async () => {
    const files = (await filesBelow(build)).map((path) => path.slice(build.length + 1));
    expect(files).toEqual(
      expect.arrayContaining([
        'index.html',
        '404.html',
        'CNAME',
        'robots.txt',
        'sitemap.xml',
        'search-index.json',
        'terms/2024/index.html',
        'cases/sample-decided/index.html',
        'cases/sample-pending/index.html'
      ])
    );
    expect(files.some((file) => file.endsWith('.pdf') || file.startsWith('documents/'))).toBe(
      false
    );
    expect(await readFile(join(build, 'CNAME'), 'utf8')).toBe('scotusbriefs.us\n');
  });

  it('renders decided, consolidated, pending, and source-link states', async () => {
    const decided = await readFile(join(build, 'cases/sample-decided/index.html'), 'utf8');
    const pending = await readFile(join(build, 'cases/sample-pending/index.html'), 'utf8');
    const index = JSON.parse(await readFile(join(build, 'search-index.json'), 'utf8')) as Array<{
      bodyText: string;
    }>;
    expect(decided).toContain('24-304 · 24-38');
    expect(decided).toContain('Official PDF');
    expect(decided).toContain('Archived copy');
    expect(pending).toContain('Awaiting decision');
    expect(pending).toContain('Guide pending');
    expect(index.map((record) => record.bodyText).join(' ')).not.toContain(
      'must never be published'
    );
  });

  it('passes the route, metadata, artifact-size, and broken-link validator', () => {
    expect(() =>
      execFileSync(
        process.execPath,
        [resolve('node_modules/tsx/dist/cli.mjs'), 'scripts/check-build.ts'],
        { cwd: process.cwd(), stdio: 'pipe' }
      )
    ).not.toThrow();
  });

  it.each(['index.html', 'cases/sample-decided/index.html', 'search/index.html'])(
    'has accessible landmarks, names, labels, and heading structure in %s',
    async (path) => {
      const html = await readFile(join(build, path), 'utf8');
      const dom = new JSDOM(html, {
        runScripts: 'outside-only',
        url: `https://scotusbriefs.us/${path}`
      });
      dom.window.eval(axe.source);
      const result = await (dom.window as unknown as { axe: typeof axe }).axe.run(
        dom.window.document,
        {
          runOnly: [
            'document-title',
            'html-has-lang',
            'landmark-one-main',
            'label',
            'link-name',
            'heading-order'
          ]
        }
      );
      expect(
        result.violations,
        result.violations.map((item) => `${item.id}: ${item.help}`).join('\n')
      ).toEqual([]);
      dom.window.close();
    }
  );
});
