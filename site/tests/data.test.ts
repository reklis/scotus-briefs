import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
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

  it('loads the canonical Python case and guide contract', async () => {
    const root = await mkdtemp(join(tmpdir(), 'scotus-canonical-'));
    const hash = 'a'.repeat(64);
    try {
      await Promise.all([
        mkdir(join(root, 'data/cases'), { recursive: true }),
        mkdir(join(root, 'data/guides'), { recursive: true }),
        mkdir(join(root, 'manifests'), { recursive: true })
      ]);
      const citation = {
        document_hash: hash,
        pages: { start: 2, end: 3 },
        evidence_ids: ['ev-1']
      };
      const complete = (heading: string, summary: string) => ({
        status: 'complete',
        heading,
        summary,
        summary_citations: [citation],
        claims: []
      });
      await writeFile(
        join(root, 'data/cases/scotus-24-7.json'),
        JSON.stringify({
          schema_version: '1.0.0',
          case_id: 'scotus-24-7',
          slug: 'scotus-24-7',
          title: 'Example v. Citizen',
          term: 2024,
          docket_numbers: ['24-7'],
          primary_docket: '24-7',
          aliases: [],
          lifecycle: 'awaiting_decision',
          dates: { argument: '2025-01-10' },
          parties: [],
          provenance: [],
          documents: [{ sha256: hash, document_type: 'opinion', current: true }]
        })
      );
      await writeFile(
        join(root, 'data/guides/scotus-24-7.json'),
        JSON.stringify({
          schema_version: '1.0.0',
          case_id: 'scotus-24-7',
          lifecycle: 'awaiting_decision',
          overview: complete('Overview', 'A plain overview.'),
          background_and_question: complete('The question', 'A supported question.'),
          party_positions: [complete('The petitioner says', 'One side argues this.')],
          oral_argument: complete('At argument', 'The justices asked questions.'),
          decision: { status: 'pending', heading: 'Decision', claims: [] },
          why_it_matters: complete('Why it matters', 'The outcome could affect citizens.'),
          glossary: [],
          sources: [citation],
          generation: {
            source_hashes: [hash],
            model_name: 'fixture',
            model_digest: 'fixture-digest',
            parameters: {},
            prompt_version: '1',
            schema_version: '1.0.0',
            extractor_version: '1',
            generated_at: '2026-01-01T00:00:00Z'
          },
          validation: {
            state: 'accepted',
            checked_at: '2026-01-01T00:00:00Z',
            checks: { schema: true },
            messages: []
          }
        })
      );
      await writeFile(
        join(root, 'manifests/documents.json'),
        JSON.stringify({
          schema_version: '1.0.0',
          documents: [
            {
              sha256: hash,
              archive_path: `documents/aa/${hash}.pdf`,
              byte_size: 12,
              document_type: 'opinion',
              sources: [{ url: 'https://www.supremecourt.gov/example.pdf' }],
              retrieved_at: '2026-01-01T00:00:00Z',
              cases: [{ case_id: 'scotus-24-7', docket_numbers: ['24-7'] }],
              media_type: 'application/pdf'
            }
          ]
        })
      );
      const catalog = await loadCatalogAt(join(root, 'data'), join(root, 'manifests'));
      const item = catalog.cases[0];
      expect(item.term).toBe('2024');
      expect(item.dates.argued).toBe('2025-01-10');
      expect(item.guide?.arguments[0].party).toBe('The petitioner says');
      expect(item.guide?.overview.citations[0]).toMatchObject({ pageStart: 2, pageEnd: 3 });
      expect(item.guide?.generatedAt).toBe('2026-01-01T00:00:00Z');
      expect(item.guide?.sourceHashes).toEqual([hash]);
      expect(item.documents[0].officialUrl).toBe('https://www.supremecourt.gov/example.pdf');
      expect(item.documents[0].archiveUrl).toContain(`/blob/dev/documents/aa/${hash}.pdf`);
    } finally {
      await rm(root, { recursive: true, force: true });
    }
  });

  it('sorts standard dockets numerically and defines application ordering', () => {
    const values = ['24A884', '24-304', '24-7', '24-38'];
    expect(values.sort(compareDockets)).toEqual(['24-7', '24-38', '24-304', '24A884']);
  });
});
