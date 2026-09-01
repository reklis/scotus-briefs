# SCOTUS Legal Briefs validation methodology and limits

## Measured coverage

The configured discovery scope is Court term 2025 with a 25-case maximum. The checked-in validation corpus evaluates 20 bounded case profiles and all required risk classes. It is fixture coverage, not a representation that 20 live merits cases were collected and not a completeness claim for the term.

Transcript publication latency cannot be measured because recorded fixtures do not include both the Court publication event and first successful poll timestamps. It is reported as unavailable rather than estimated. Live discovery remains explicitly unvalidated.

## Whole-case and citation methodology

One public URL represents the durable case. If the Court heard the matter more than once, generation waits for every discovered session's complete accepted transcript. Claims retain their argument-session identity. The page presents sessions chronologically, explains each separately, and permits cross-session comparisons only when claims from the relevant transcripts support them. A later argument never overwrites the earlier one.

A public characterization is generated only from an approved claim. Each claim resolves to an accepted official Court URL, evidence kind, argument session where applicable, document revision, page/line range, attribution, legal status, certainty, and source observation IDs. Quotations require exact bounded-evidence support; the MVP is paraphrase-first. Authorities absent from evidence are rejected, and transcript evidence cannot prove a holding or final disposition.

## Analytical limits

Oral argument is exploratory and does not reveal a vote or decision. Questions may be hypothetical or adversarial. The system does not score ideology or tone, predict outcomes as fact, resolve disputed facts, provide litigation strategy, or offer personalized legal advice. PDF layout changes, incomplete Court metadata, consolidated matters, unusual labels, scanned documents, and model omissions may delay or suppress analysis. Unsupported sections are omitted.

## Official-record distinction

SCOTUS Legal Briefs is automated, delayed, incomplete, non-authoritative analysis. It is not affiliated with the Supreme Court, is not an official Court record, and is not legal advice. Readers must follow descriptive links to `www.supremecourt.gov` for official transcripts, dockets, orders, and opinions.

## Private/public boundary

Copied PDFs, complete extracted text, parser artifacts, prompts, object keys, rejected claims, and credentials remain private. Public output contains only validated projections and official provenance links. Sensitive details unnecessary to explain the legal issue are generalized or suppressed.
