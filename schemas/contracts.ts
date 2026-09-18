// Canonical consumer types. JSON Schema files remain the validation authority.
export type Sha256 = string;
export type DocumentType =
  | 'oral_argument_transcript' | 'opinion' | 'order' | 'merits_brief'
  | 'reply_brief' | 'amicus_brief' | 'petition' | 'response'
  | 'appendix' | 'other' | 'unknown';

export interface PageRange { start: number; end: number }
export interface CaseAssociation {
  case_id?: string | null;
  docket_numbers: string[];
  historical_group?: string | null;
}
export interface SourceIdentity {
  url?: string | null;
  source_id?: string | null;
  page_url?: string | null;
  official_filename?: string | null;
  import_path?: string | null;
}
export interface DocumentManifestEntry {
  sha256: Sha256;
  archive_path: string;
  byte_size: number;
  document_type: DocumentType;
  sources: SourceIdentity[];
  retrieved_at: string;
  cases: CaseAssociation[];
  supersedes?: Sha256 | null;
  media_type: 'application/pdf';
}
export interface DocumentManifest { schema_version: '1.0.0'; documents: DocumentManifestEntry[] }

export type Lifecycle = 'pending' | 'scheduled' | 'argued' | 'awaiting_decision' | 'decided' | 'dismissed' | 'unresolved';
export interface CaseDates { filed?: string | null; granted?: string | null; argument?: string | null; decision?: string | null }
export interface MetadataProvenance {
  field: string; source_url?: string | null; document_hash?: Sha256 | null;
  observed_at?: string | null; method: 'official' | 'imported' | 'extracted' | 'manual';
  confidence: number;
}
export interface NormalizedCase {
  schema_version: '1.0.0'; case_id: string; slug: string; title: string;
  term?: number | null; docket_numbers: string[]; primary_docket?: string | null;
  aliases: string[]; lifecycle: Lifecycle; dates: CaseDates;
  parties: Array<{name: string; role?: string | null}>;
  provenance: MetadataProvenance[];
  documents: Array<{sha256: Sha256; document_type: DocumentType; current: boolean}>;
  unresolved_group?: string | null;
}
export type EvidenceKind = 'fact' | 'allegation' | 'party_argument' | 'amicus_argument' | 'procedural_event' | 'justice_question' | 'holding' | 'concurrence' | 'dissent' | 'stated_consequence';
export interface EvidenceRecord {
  schema_version: '1.0.0'; evidence_id: string; case_id: string;
  document_hash: Sha256; pages: PageRange; kind: EvidenceKind;
  attribution: string; text: string; confidence: number;
  status: 'supported' | 'uncertain' | 'unavailable';
  opinion_part?: 'majority' | 'plurality' | 'concurrence' | 'dissent' | 'per_curiam' | null;
}
export interface Citation { document_hash: Sha256; pages: PageRange; evidence_ids: string[] }
export interface MaterialClaim { text: string; attribution?: string | null; citations: Citation[] }
export interface GuideSection {
  status: 'complete' | 'pending' | 'source_limited' | 'not_applicable';
  heading: string; summary?: string | null; summary_citations: Citation[];
  claims: MaterialClaim[];
}
export interface CitizenGuide {
  schema_version: '1.0.0'; case_id: string; lifecycle: Lifecycle;
  overview: GuideSection; background_and_question: GuideSection;
  party_positions: GuideSection[]; oral_argument: GuideSection;
  decision: GuideSection; why_it_matters: GuideSection;
  glossary: Array<{term: string; definition: string; citations: Citation[]}>;
  sources: Citation[];
  generation: {
    source_hashes: Sha256[]; model_name: string; model_digest: string;
    parameters: Record<string, unknown>; prompt_version: string;
    schema_version: string; extractor_version: string; generated_at: string;
  };
  validation: {
    state: 'candidate' | 'accepted' | 'rejected'; checked_at: string;
    checks: Record<string, boolean>; messages: string[];
  };
}
