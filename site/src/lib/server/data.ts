import { readdir, readFile } from 'node:fs/promises';
import { resolve, relative, sep } from 'node:path';
import type {
  ArgumentSection,
  Citation,
  DocumentSource,
  GuideSection,
  Lifecycle,
  PublicCase,
  PublicGuide,
  SearchRecord,
  SectionState,
  TermSummary
} from '$lib/types';

const REPOSITORY_BLOB_ROOT = 'https://github.com/reklis/scotus-briefs/blob/dev/';
const SAFE_SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const LIFECYCLES = new Set<Lifecycle>([
  'pending',
  'scheduled',
  'argued',
  'awaiting-decision',
  'decided',
  'unresolved'
]);

type Json = Record<string, unknown>;

export interface Catalog {
  cases: PublicCase[];
  terms: TermSummary[];
  searchIndex: SearchRecord[];
}

function object(value: unknown, context: string): Json {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`${context} must be a JSON object`);
  }
  return value as Json;
}

function requiredString(value: unknown, context: string): string {
  if (typeof value !== 'string' || !value.trim()) throw new Error(`${context} must be a string`);
  return value.trim();
}

function optionalString(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value.trim() : undefined;
}

function strings(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === 'string' && Boolean(item.trim()));
}

async function filesBelow(directory: string): Promise<string[]> {
  try {
    const entries = await readdir(directory, { withFileTypes: true });
    const paths = await Promise.all(
      entries.map(async (entry) => {
        const path = resolve(directory, entry.name);
        if (entry.isDirectory()) return filesBelow(path);
        return entry.isFile() && (entry.name.endsWith('.json') || entry.name.endsWith('.jsonl'))
          ? [path]
          : [];
      })
    );
    return paths.flat().sort();
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return [];
    throw error;
  }
}

async function jsonRecords(directory: string): Promise<Array<{ value: unknown; source: string }>> {
  const records: Array<{ value: unknown; source: string }> = [];
  for (const file of await filesBelow(directory)) {
    const text = await readFile(file, 'utf8');
    if (file.endsWith('.jsonl')) {
      for (const [lineIndex, line] of text.split(/\r?\n/).entries()) {
        if (!line.trim()) continue;
        try {
          records.push({ value: JSON.parse(line), source: `${file}:${lineIndex + 1}` });
        } catch {
          throw new Error(`Invalid JSON in ${file}:${lineIndex + 1}`);
        }
      }
      continue;
    }
    let parsed: unknown;
    try {
      parsed = JSON.parse(text);
    } catch {
      throw new Error(`Invalid JSON in ${file}`);
    }
    const values = Array.isArray(parsed) ? parsed : [parsed];
    values.forEach((value) => records.push({ value, source: file }));
  }
  return records;
}

function archiveUrl(path: string): string {
  const encoded = path.split(/[\\/]/).map(encodeURIComponent).join('/');
  return `${REPOSITORY_BLOB_ROOT}${encoded}`;
}

function normalizeManifest(raw: unknown, source: string): DocumentSource | undefined {
  const item = object(raw, source);
  const sha = optionalString(item.sha256 ?? item.hash ?? item.document_hash);
  if (!sha) return undefined;
  const path = optionalString(item.archive_path ?? item.path);
  return {
    id: sha,
    sha256: sha,
    title:
      optionalString(item.title ?? item.official_filename ?? item.filename) ??
      `${optionalString(item.document_type) ?? 'Court document'} (${sha.slice(0, 8)})`,
    type: optionalString(item.document_type ?? item.type),
    officialUrl: optionalString(item.source_url ?? item.official_url ?? item.url),
    archivePath: path,
    archiveUrl: path ? archiveUrl(path) : undefined
  };
}

function lifecycle(value: unknown): Lifecycle {
  const normalized = optionalString(value)?.toLowerCase().replaceAll('_', '-');
  const aliases: Record<string, Lifecycle> = {
    'awaiting-opinion': 'awaiting-decision',
    'awaiting decision': 'awaiting-decision',
    granted: 'scheduled',
    dismissed: 'unresolved'
  };
  const result = aliases[normalized ?? ''] ?? normalized;
  return result && LIFECYCLES.has(result as Lifecycle) ? (result as Lifecycle) : 'unresolved';
}

function normalizeDocument(
  value: unknown,
  manifests: Map<string, DocumentSource>,
  context: string
): DocumentSource {
  if (typeof value === 'string') {
    return (
      manifests.get(value) ?? {
        id: value,
        sha256: value,
        title: `Archived court document (${value.slice(0, 8)})`
      }
    );
  }
  const item = object(value, context);
  const sha = optionalString(item.sha256 ?? item.hash ?? item.document_hash);
  const id = requiredString(item.id ?? item.document_id ?? sha, `${context}.id`);
  const manifest = (sha && manifests.get(sha)) || manifests.get(id);
  const path = optionalString(item.archive_path ?? item.path) ?? manifest?.archivePath;
  return {
    id,
    sha256: sha ?? manifest?.sha256,
    title:
      optionalString(item.title ?? item.name ?? item.official_filename) ??
      manifest?.title ??
      `${optionalString(item.type ?? item.document_type) ?? 'Court document'} (${id.slice(0, 8)})`,
    type: optionalString(item.type ?? item.document_type) ?? manifest?.type,
    officialUrl:
      optionalString(item.official_url ?? item.source_url ?? item.url) ?? manifest?.officialUrl,
    archivePath: path,
    archiveUrl:
      optionalString(item.archive_url) ??
      manifest?.archiveUrl ??
      (path ? archiveUrl(path) : undefined)
  };
}

function normalizeCase(
  raw: unknown,
  source: string,
  manifests: Map<string, DocumentSource>
): PublicCase | undefined {
  const item = object(raw, source);
  const id = requiredString(item.id ?? item.case_id, `${source}.id`);
  const slug = requiredString(item.slug ?? id, `${source}.slug`);
  if (!SAFE_SLUG.test(slug)) throw new Error(`${source}.slug must be a safe lowercase URL slug`);
  const dockets = strings(item.docket_numbers ?? item.dockets);
  const primary = optionalString(item.primary_docket ?? item.docket_number);
  if (primary && !dockets.includes(primary)) dockets.unshift(primary);
  // Unresolved imports are durable archive records, not yet publishable cases.
  if (!dockets.length || item.term === null || item.term === undefined) return undefined;
  const dateValues: Json = item.dates ? object(item.dates, `${source}.dates`) : ({} as Json);
  const documentValues = Array.isArray(item.documents)
    ? item.documents
    : Array.isArray(item.document_references)
      ? item.document_references
      : Array.isArray(item.document_ids)
        ? item.document_ids
        : [];
  const documents = documentValues.map((document, index) =>
    normalizeDocument(document, manifests, `${source}.documents[${index}]`)
  );
  const status = lifecycle(item.lifecycle ?? item.status);
  return {
    id,
    slug,
    title: requiredString(item.title ?? item.case_name, `${source}.title`),
    term:
      typeof item.term === 'number'
        ? String(item.term)
        : requiredString(item.term, `${source}.term`),
    dockets,
    aliases: strings(item.aliases),
    lifecycle: status,
    dates: {
      argued: optionalString(
        dateValues.argued ?? dateValues.argument ?? dateValues.argument_date ?? item.argument_date
      ),
      decided: optionalString(
        dateValues.decided ?? dateValues.decision ?? dateValues.decision_date ?? item.decision_date
      ),
      filed: optionalString(dateValues.filed ?? item.filed_date),
      scheduled: optionalString(dateValues.scheduled ?? item.scheduled_date)
    },
    documents,
    updatedAt: optionalString(item.updated_at ?? item.modified_at)
  };
}

function sectionState(value: unknown, hasContent: boolean): SectionState {
  const normalized = optionalString(value)?.toLowerCase().replaceAll('_', '-');
  if (normalized === 'pending' || normalized === 'source-limited') return normalized;
  if (normalized === 'unavailable' || normalized === 'limited') return 'source-limited';
  return hasContent ? 'available' : 'source-limited';
}

function normalizeCitation(raw: unknown): Citation | undefined {
  if (typeof raw === 'string') return { documentId: raw };
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return undefined;
  const item = raw as Json;
  const documentId = optionalString(
    item.document_id ?? item.document_hash ?? item.sha256 ?? item.source_hash
  );
  if (!documentId) return undefined;
  const range = Array.isArray(item.pages) ? item.pages : undefined;
  const pageObject =
    item.pages && typeof item.pages === 'object' && !Array.isArray(item.pages)
      ? (item.pages as Json)
      : undefined;
  const start = Number(item.page_start ?? item.page ?? range?.[0] ?? pageObject?.start);
  const end = Number(item.page_end ?? range?.[1] ?? pageObject?.end);
  return {
    documentId,
    pageStart: Number.isInteger(start) && start > 0 ? start : undefined,
    pageEnd: Number.isInteger(end) && end > 0 ? end : undefined,
    label: optionalString(item.label)
  };
}

function section(raw: unknown, fallbackState: SectionState = 'source-limited'): GuideSection {
  if (typeof raw === 'string') {
    return { state: 'available', paragraphs: [raw], citations: [] };
  }
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    return { state: fallbackState, paragraphs: [], citations: [] };
  }
  const item = raw as Json;
  const direct = item.paragraphs ?? item.content ?? item.text ?? item.summary;
  let paragraphs = typeof direct === 'string' ? [direct] : strings(direct);
  const contentRecords = Array.isArray(direct)
    ? direct.filter(
        (entry): entry is Json =>
          Boolean(entry) && typeof entry === 'object' && !Array.isArray(entry)
      )
    : [];
  const claims = Array.isArray(item.claims) ? item.claims : [];
  if (!paragraphs.length) {
    paragraphs = [...contentRecords, ...claims].flatMap((claim) => {
      if (typeof claim === 'string') return [claim];
      if (claim && typeof claim === 'object' && !Array.isArray(claim)) {
        const text = optionalString((claim as Json).text ?? (claim as Json).claim);
        return text ? [text] : [];
      }
      return [];
    });
  }
  const rawCitations = [
    ...(Array.isArray(item.citations) ? item.citations : []),
    ...(Array.isArray(item.summary_citations) ? item.summary_citations : []),
    ...[...contentRecords, ...claims].flatMap((claim) =>
      claim &&
      typeof claim === 'object' &&
      !Array.isArray(claim) &&
      Array.isArray((claim as Json).citations)
        ? ((claim as Json).citations as unknown[])
        : []
    )
  ];
  const citations = rawCitations
    .map(normalizeCitation)
    .filter((citation): citation is Citation => Boolean(citation));
  return {
    state: sectionState(item.status ?? item.state, paragraphs.length > 0),
    paragraphs,
    citations
  };
}

function normalizeGuide(raw: unknown, source: string): PublicGuide | undefined {
  const item = object(raw, source);
  const validation =
    item.validation && typeof item.validation === 'object' && !Array.isArray(item.validation)
      ? (item.validation as Json)
      : {};
  const accepted = optionalString(
    item.validation_status ??
      item.status ??
      item.state ??
      validation.state ??
      validation.status ??
      validation.result
  )?.toLowerCase();
  const acceptedBoolean = item.accepted === true || validation.accepted === true;
  if (
    !acceptedBoolean &&
    accepted !== 'accepted' &&
    accepted !== 'passed' &&
    accepted !== 'valid'
  ) {
    return undefined;
  }
  const caseId = requiredString(item.case_id ?? item.caseId, `${source}.case_id`);
  const sections =
    item.sections && typeof item.sections === 'object' && !Array.isArray(item.sections)
      ? (item.sections as Json)
      : item;
  const argumentRaw =
    sections.arguments ??
    sections.party_arguments ??
    sections.party_positions ??
    (sections.petitioner || sections.respondent
      ? { Petitioner: sections.petitioner, Respondent: sections.respondent }
      : []);
  const argumentValues: Array<[string, unknown]> = Array.isArray(argumentRaw)
    ? argumentRaw.map((value, index) => [`Side ${index + 1}`, value])
    : argumentRaw && typeof argumentRaw === 'object'
      ? Object.entries(argumentRaw as Json)
      : [];
  const argumentsNormalized: ArgumentSection[] = argumentValues.map(([label, value]) => {
    const itemValue =
      value && typeof value === 'object' && !Array.isArray(value) ? (value as Json) : {};
    return {
      ...section(value),
      party: optionalString(itemValue.party ?? itemValue.heading ?? itemValue.label) ?? label
    };
  });
  const glossaryRaw = sections.glossary ?? sections.terms ?? [];
  const glossary = Array.isArray(glossaryRaw)
    ? glossaryRaw.flatMap((entry) => {
        if (!entry || typeof entry !== 'object' || Array.isArray(entry)) return [];
        const value = entry as Json;
        const term = optionalString(value.term ?? value.name);
        const definition = optionalString(value.definition ?? value.meaning);
        return term && definition ? [{ term, definition }] : [];
      })
    : glossaryRaw && typeof glossaryRaw === 'object'
      ? Object.entries(glossaryRaw as Json).flatMap(([term, definition]) =>
          typeof definition === 'string' ? [{ term, definition }] : []
        )
      : [];
  return {
    caseId,
    generatedAt: optionalString(
      item.generated_at ??
        item.updated_at ??
        (item.generation && typeof item.generation === 'object' && !Array.isArray(item.generation)
          ? (item.generation as Json).generated_at
          : undefined)
    ),
    overview: section(sections.overview),
    background: section(
      sections.background ?? sections.question ?? sections.background_and_question
    ),
    arguments: argumentsNormalized,
    oralArgument: section(sections.oral_argument, 'pending'),
    decision: section(sections.decision, 'pending'),
    significance: section(sections.significance ?? sections.why_it_matters),
    glossary,
    sourceHashes: strings(
      item.source_hashes ??
        (item.generation && typeof item.generation === 'object' && !Array.isArray(item.generation)
          ? (item.generation as Json).source_hashes
          : undefined)
    )
  };
}

export function docketSortKey(docket: string): [number, number, number, string] {
  const standard = docket.match(/^(\d+)-(\d+)$/i);
  if (standard) return [Number(standard[1]), 0, Number(standard[2]), docket];
  const application = docket.match(/^(\d+)A(\d+)$/i);
  if (application) return [Number(application[1]), 1, Number(application[2]), docket];
  return [Number.MAX_SAFE_INTEGER, 2, Number.MAX_SAFE_INTEGER, docket];
}

export function compareDockets(left: string, right: string): number {
  const a = docketSortKey(left);
  const b = docketSortKey(right);
  return a[0] - b[0] || a[1] - b[1] || a[2] - b[2] || a[3].localeCompare(b[3]);
}

function guideText(guide: PublicGuide | undefined): string {
  if (!guide) return '';
  return [
    ...guide.overview.paragraphs,
    ...guide.background.paragraphs,
    ...guide.arguments.flatMap((argument) => [argument.party, ...argument.paragraphs]),
    ...guide.oralArgument.paragraphs,
    ...guide.decision.paragraphs,
    ...guide.significance.paragraphs,
    ...guide.glossary.flatMap((entry) => [entry.term, entry.definition])
  ].join(' ');
}

export function createSearchRecord(item: PublicCase): SearchRecord {
  return {
    id: item.id,
    slug: item.slug,
    title: item.title,
    term: item.term,
    dockets: item.dockets,
    lifecycle: item.lifecycle,
    titleText: [item.title, ...item.aliases].join(' ').toLowerCase(),
    docketText: item.dockets.join(' ').toLowerCase(),
    bodyText: guideText(item.guide).toLowerCase()
  };
}

export async function loadCatalogAt(dataRoot: string, manifestRoot?: string): Promise<Catalog> {
  const manifestRecords = manifestRoot ? await jsonRecords(manifestRoot) : [];
  const manifests = new Map<string, DocumentSource>();
  for (const record of manifestRecords) {
    const manifest = normalizeManifest(record.value, record.source);
    if (manifest) manifests.set(manifest.id, manifest);
  }
  const caseRecords = await jsonRecords(resolve(dataRoot, 'cases'));
  const guideRecords = await jsonRecords(resolve(dataRoot, 'guides'));
  const cases = caseRecords
    .map(({ value, source }) => normalizeCase(value, source, manifests))
    .filter((item): item is PublicCase => Boolean(item));
  const ids = new Set<string>();
  const slugs = new Set<string>();
  for (const item of cases) {
    if (ids.has(item.id)) throw new Error(`Duplicate case id: ${item.id}`);
    if (slugs.has(item.slug)) throw new Error(`Duplicate case slug: ${item.slug}`);
    ids.add(item.id);
    slugs.add(item.slug);
  }
  const guides = new Map<string, PublicGuide>();
  for (const { value, source } of guideRecords) {
    const guide = normalizeGuide(value, source);
    if (!guide) continue;
    if (!ids.has(guide.caseId)) throw new Error(`${source} refers to unknown case ${guide.caseId}`);
    if (guides.has(guide.caseId))
      throw new Error(`Multiple accepted guides for case ${guide.caseId}`);
    guides.set(guide.caseId, guide);
  }
  for (const item of cases) {
    item.guide = guides.get(item.id);
    if (
      item.lifecycle !== 'decided' &&
      item.guide?.decision.state === 'available' &&
      item.guide.decision.paragraphs.length
    ) {
      throw new Error(`Undecided case ${item.id} has an accepted decision section`);
    }
    if (item.guide) {
      const sections = [
        item.guide.overview,
        item.guide.background,
        ...item.guide.arguments,
        item.guide.oralArgument,
        item.guide.decision,
        item.guide.significance
      ];
      const documentIds = new Set(
        item.documents.flatMap((document) => [document.id, document.sha256])
      );
      for (const citation of sections.flatMap((value) => value.citations)) {
        if (!documentIds.has(citation.documentId)) {
          throw new Error(`Guide for ${item.id} cites unknown document ${citation.documentId}`);
        }
      }
    }
    item.documents.sort((a, b) => a.title.localeCompare(b.title));
  }
  cases.sort(
    (a, b) =>
      b.term.localeCompare(a.term, undefined, { numeric: true }) ||
      compareDockets(a.dockets[0], b.dockets[0]) ||
      a.title.localeCompare(b.title)
  );
  const termNames = [...new Set(cases.map((item) => item.term))].sort((a, b) =>
    b.localeCompare(a, undefined, { numeric: true })
  );
  const terms = termNames.map((term, index) => ({
    term,
    caseCount: cases.filter((item) => item.term === term).length,
    latest: index === 0
  }));
  return { cases, terms, searchIndex: cases.map(createSearchRecord) };
}

let catalogPromise: Promise<Catalog> | undefined;
export function loadCatalog(): Promise<Catalog> {
  if (!catalogPromise) {
    const repositoryRoot = resolve(process.cwd(), '..');
    const dataRoot = process.env.SCOTUS_DATA_ROOT
      ? resolve(process.env.SCOTUS_DATA_ROOT)
      : resolve(repositoryRoot, 'data');
    const manifestRoot = process.env.SCOTUS_MANIFEST_ROOT
      ? resolve(process.env.SCOTUS_MANIFEST_ROOT)
      : resolve(repositoryRoot, 'manifests');
    catalogPromise = loadCatalogAt(dataRoot, manifestRoot);
  }
  return catalogPromise;
}

export function displaySourcePath(path: string, root: string): string {
  return relative(root, path).split(sep).join('/');
}
