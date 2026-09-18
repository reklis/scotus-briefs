export type Lifecycle =
  | 'pending'
  | 'scheduled'
  | 'argued'
  | 'awaiting-decision'
  | 'decided'
  | 'unresolved';
export type SectionState = 'available' | 'pending' | 'source-limited';

export interface DocumentSource {
  id: string;
  sha256?: string;
  title: string;
  type?: string;
  officialUrl?: string;
  archivePath?: string;
  archiveUrl?: string;
}

export interface Citation {
  documentId: string;
  pageStart?: number;
  pageEnd?: number;
  label?: string;
}

export interface GuideSection {
  state: SectionState;
  paragraphs: string[];
  citations: Citation[];
}

export interface ArgumentSection extends GuideSection {
  party: string;
}

export interface GlossaryEntry {
  term: string;
  definition: string;
}

export interface PublicGuide {
  caseId: string;
  generatedAt?: string;
  overview: GuideSection;
  background: GuideSection;
  arguments: ArgumentSection[];
  oralArgument: GuideSection;
  decision: GuideSection;
  significance: GuideSection;
  glossary: GlossaryEntry[];
  sourceHashes: string[];
}

export interface CaseDates {
  argued?: string;
  decided?: string;
  filed?: string;
  scheduled?: string;
}

export interface PublicCase {
  id: string;
  slug: string;
  title: string;
  term: string;
  dockets: string[];
  aliases: string[];
  lifecycle: Lifecycle;
  dates: CaseDates;
  documents: DocumentSource[];
  guide?: PublicGuide;
  updatedAt?: string;
}

export interface TermSummary {
  term: string;
  caseCount: number;
  latest: boolean;
}

export interface SearchRecord {
  id: string;
  slug: string;
  title: string;
  term: string;
  dockets: string[];
  lifecycle: Lifecycle;
  titleText: string;
  docketText: string;
  bodyText: string;
}
