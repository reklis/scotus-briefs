import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { compareDockets, loadCatalogAt } from '../src/lib/server/data';

const fixtureRoot = resolve('tests/fixtures');

describe('build-time catalog', () => {
  it('validates and normalizes cases, manifests, and accepted guides', async () => {
    const catalog = await loadCatalogAt(
      resolve(fixtureRoot, 'data'),
      resolve(fixtureRoot, 'manifests')
    );
    expect(catalog.cases).toHaveLength(2);
    const decided = catalog.cases.find((item) => item.id === 'sample-decided');
    const pending = catalog.cases.find((item) => item.id === 'sample-pending');
    expect(decided?.guide?.decision.paragraphs[0]).toContain('wrong legal test');
    expect(decided?.documents[0].officialUrl).toContain('supremecourt.gov');
    expect(decided?.documents[0].archiveUrl).toContain('/blob/dev/documents/aa/');
    expect(pending?.guide).toBeUndefined();
    expect(pending?.lifecycle).toBe('awaiting-decision');
  });

  it('sorts standard dockets numerically and defines application ordering', () => {
    const values = ['24A884', '24-304', '24-7', '24-38'];
    expect(values.sort(compareDockets)).toEqual(['24-7', '24-38', '24-304', '24A884']);
  });
});
