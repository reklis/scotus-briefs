## Why

The Supreme Court publishes complete official transcripts, dockets, opinions, and orders that can support a reliable evidence-grounded product without audio capture or speech-to-text. A focused “SCOTUS Legal Briefs” site can turn difficult, lengthy oral arguments into accessible legal analysis while carefully distinguishing advocates’ positions, justices’ questions, and actual Court holdings.

## What Changes

- Narrow the active product to Supreme Court oral arguments and related official Court records; House, DC Council, mayoral, and radio sources are outside this MVP.
- Track the Court’s official argument index and ingest newly linked full official transcript PDFs with deterministic identities, integrity checks, revisions, and retention.
- Parse transcript pages, line structure, and supported speaker labels directly from the Court’s official transcripts; do not download argument audio or run speech-to-text.
- Ingest docket metadata, question-presented material, official argument transcripts, and later opinions/orders as separate evidence types.
- Extract speaker-aware, citation-bearing observations about procedural posture, legal questions, advocate positions, justice questions, doctrinal themes, concessions, disputed facts, and next procedural steps.
- Use the official OpenAI API to generate a structured brief for each argued case containing case background, the core question, competing arguments, supported justice questions, pivotal exchanges, relevant authorities, uncertainty, and what happens next.
- Write for non-lawyers in direct everyday language, explain unavoidable legal terms in context, use short sentences and descriptive headings, and reject unexplained legalese.
- Publish a term/case-oriented website under the working title **SCOTUS Legal Briefs**, with search/filtering, source links, revisions, and a clear automated-analysis/not-legal-advice disclaimer.
- Never portray oral-argument questions as holdings, infer a justice’s vote, predict an outcome as fact, fabricate quotations/citations, or present the analysis as an official Court record.
- Keep copied documents and extracted transcript text private; do not redistribute full transcripts, and link readers to the official Court pages.
- Preserve public case captions and supported public legal roles while minimizing unnecessary repetition of sensitive personal facts, especially involving minors, victims, medical details, or sealed/redacted matters.

## Capabilities

### New Capabilities

- `scotus-argument-discovery`: Discover and version official oral-argument cases, full transcripts, dockets, and later opinions/orders from approved Court-hosted pages.
- `scotus-document-ingestion`: Reliably download, validate, privately parse, retain, and reprocess official Supreme Court transcript and case-document revisions.
- `scotus-argument-analysis`: Derive speaker-aware, page/line-grounded legal observations from official transcripts without confusing questions, argument, and holdings.
- `legal-brief-generation`: Build and validate structured, source-grounded case analyses with legal-status, citation, uncertainty, and sensitivity safeguards.
- `scotus-briefs-site`: Publish searchable term and case pages with official provenance, revisions, corrections, and non-authoritative/not-legal-advice disclosures.

### Modified Capabilities

None.

## Impact

- Reuses the proceedings contracts, fail-closed registry, Supreme Court source review/adapter, PostgreSQL jobs, private object storage, evidence validation, grounded generation, public projections, and deployment foundation already present in the repository.
- Adds Supreme Court-specific PDF/document ingestion, transcript parsing, docket/document evidence, legal extraction schemas, case correlation, policy, public contracts, templates, metrics, and validation fixtures.
- Changes the active product configuration and site information architecture from hourly local news to case-oriented Supreme Court legal analysis.
- Leaves prior radio and multi-source proceedings implementations dormant and keeps all non-Supreme sources disabled.
