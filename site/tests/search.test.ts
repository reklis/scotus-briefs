import { describe, expect, it } from 'vitest';
import { searchCases } from '../src/lib/search';
import type { SearchRecord } from '../src/lib/types';

const records: SearchRecord[] = [
  {
    id: 'docket',
    slug: 'docket',
    title: 'Unrelated title',
    term: '2024',
    dockets: ['24-304'],
    lifecycle: 'decided',
    titleText: 'unrelated title',
    docketText: '24-304',
    bodyText: 'speech issue appears here'
  },
  {
    id: 'title',
    slug: 'title',
    title: 'Speech Association',
    term: '2024',
    dockets: ['24-999'],
    lifecycle: 'pending',
    titleText: 'speech association',
    docketText: '24-999',
    bodyText: 'other matter'
  },
  {
    id: 'body',
    slug: 'body',
    title: 'Agency Case',
    term: '2023',
    dockets: ['23-1'],
    lifecycle: 'decided',
    titleText: 'agency case',
    docketText: '23-1',
    bodyText: 'a dispute concerning speech rights in a public forum'
  }
];

describe('client search', () => {
  it('ranks exact docket matches above body matches', () => {
    expect(searchCases(records, '24-304').map((item) => item.id)).toEqual(['docket']);
  });

  it('ranks title matches above guide body text and returns context', () => {
    const results = searchCases(records, 'speech');
    expect(results[0].id).toBe('title');
    expect(results.map((item) => item.id)).toEqual(expect.arrayContaining(['docket', 'body']));
    expect(results.find((item) => item.id === 'body')?.excerpt).toContain('speech');
  });

  it('returns an empty list for blank and unmatched queries', () => {
    expect(searchCases(records, '')).toEqual([]);
    expect(searchCases(records, 'habeas')).toEqual([]);
  });
});
