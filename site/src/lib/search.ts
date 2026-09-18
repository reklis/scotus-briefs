import type { SearchRecord } from '$lib/types';

export interface RankedResult extends SearchRecord {
  score: number;
  excerpt: string;
}

function words(query: string): string[] {
  return [...new Set(query.toLowerCase().trim().split(/\s+/).filter(Boolean))];
}

function context(text: string, terms: string[]): string {
  if (!text) return '';
  const first = terms
    .map((term) => text.indexOf(term))
    .filter((position) => position >= 0)
    .sort((a, b) => a - b)[0];
  if (first === undefined) return text.slice(0, 180);
  const start = Math.max(0, first - 65);
  const end = Math.min(text.length, first + 130);
  return `${start ? '…' : ''}${text.slice(start, end).trim()}${end < text.length ? '…' : ''}`;
}

export function searchCases(records: SearchRecord[], query: string): RankedResult[] {
  const terms = words(query);
  if (!terms.length) return [];
  return records
    .flatMap((record) => {
      let score = 0;
      for (const term of terms) {
        if (record.docketText === term) score += 120;
        else if (record.docketText.includes(term)) score += 70;
        if (record.titleText.startsWith(term)) score += 50;
        else if (record.titleText.includes(term)) score += 35;
        if (record.bodyText.includes(term)) score += 5;
      }
      const searchable = `${record.docketText} ${record.titleText} ${record.bodyText}`;
      if (!terms.every((term) => searchable.includes(term))) return [];
      return [{ ...record, score, excerpt: context(record.bodyText || record.titleText, terms) }];
    })
    .sort((a, b) => b.score - a.score || a.title.localeCompare(b.title));
}
