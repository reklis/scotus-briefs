## ADDED Requirements

### Requirement: Deterministic reader-guide planning
The system SHALL create a deterministic reader-guide plan from approved claims before requesting public prose. Each section plan MUST define its reader purpose, exact heading, allowed and required claim types and legal statuses, bounded relevant claim packet, and any required actor/action slots. The model MUST NOT choose case status, chronology, section order, argument-session identity, official links, or citation identity.

#### Scenario: Long argued-case ledger
- **WHEN** an argued case has more approved claims than the configured writing context permits
- **THEN** the planner selects bounded nonduplicative claims that preserve every established side, required legal question, procedural path, and argument-session coverage without sending the complete ledger

#### Scenario: Unsupported required section
- **WHEN** approved claims cannot support a required section's reader purpose and legal role
- **THEN** planning fails closed without asking the writer to invent or fill the section

#### Scenario: Multiple argument sessions
- **WHEN** a case has argument and reargument sessions
- **THEN** each argument packet contains only claims tied to its real session and the output preserves chronological session order

### Requirement: LLM performs plain-language translation
The writer SHALL receive compact approved section packets and SHALL translate their case-specific meaning into direct everyday language for a reader without legal training. A precise legal term MUST be omitted when ordinary words preserve the meaning; when the term itself is necessary, the same sentence MUST explain what it does in that case. Every output field MUST retain only the supplied supporting claim IDs.

#### Scenario: Ordinary wording preserves meaning
- **WHEN** an approved claim says that the Court remanded a case
- **THEN** the writer states that the Court sent the case back to a lower court while the action validator preserves the canonical remand result

#### Scenario: Necessary legal term
- **WHEN** naming an injunction is necessary to distinguish the operative order
- **THEN** the sentence immediately explains that the injunction is a court order that blocks or requires the case-specific action

#### Scenario: Source uses dense legal language
- **WHEN** a claim contains lawyer-facing doctrine or procedural shorthand
- **THEN** the writer does not satisfy grounding merely by copying that wording and instead preserves the supported people, event, rule, action, and result in ordinary language

### Requirement: Targeted private repair
When a draft field fails validation, the system SHALL preserve valid fields and MAY request a bounded repair of only the rejected field using its section packet, rejected text, and a concrete process-local diagnostic. Every repaired field MUST pass the complete grounding, legal-role, status, privacy, reader-language, and length policy before assembly.

#### Scenario: One paragraph contains unexplained jurisdiction language
- **WHEN** one paragraph fails because it uses `jurisdiction` without explaining a court's power to hear the case
- **THEN** the repair request contains only that field and its support packet and leaves all previously valid fields byte-for-byte unchanged

#### Scenario: Repair changes the Court action
- **WHEN** a stylistic repair changes the actor, canonical action, operative object, negation, or procedural effect
- **THEN** action validation rejects the repair even if its language is easier to read

#### Scenario: Repair budget is exhausted
- **WHEN** the writer cannot repair all invalid fields within configured field, case, call, token, or runtime limits
- **THEN** no partial draft is published and the last-known-good public case remains active

### Requirement: Versioned reader-prose gate
Every newly generated or changed public title, summary, section paragraph, and argument paragraph MUST pass a versioned deterministic reader-prose policy. The policy SHALL reject unexplained legal terminology, lawyer-facing phrases, processing commentary, unsupported no-decision statements, unsupported future predictions, excessive length, repeated fragments, and prose irrelevant to its section purpose.

#### Scenario: Unexplained courtroom shorthand
- **WHEN** candidate prose uses terms such as waiver, pretext, rebuttal, finality, standing, jurisdiction, habeas, vacatur, or sovereign immunity without a same-sentence case-specific explanation
- **THEN** publication is rejected with a fixed safe code identifying the reader-language rule

#### Scenario: Explained central concept
- **WHEN** a central legal term is followed in the same sentence by an accurate ordinary-language explanation grounded in the cited claims
- **THEN** the term does not fail the reader-language rule by itself

#### Scenario: Unsupported future impact
- **WHEN** prose says that the Court will decide, clarify, establish, guide, affect, or change something without an approved claim establishing that future event
- **THEN** publication is rejected as unsupported prediction

#### Scenario: Internal process prose
- **WHEN** prose refers to the approved record, claims, extraction, model, prompt, schema, or unavailable processing details
- **THEN** publication is rejected even when the statement is literally true about the pipeline

#### Scenario: Official caption contains a legal term
- **WHEN** a disposition-only title is deterministically fixed to the exact official caption
- **THEN** the caption is exempt from title rewriting but every editorial title, summary, heading, and paragraph remains subject to the reader-prose policy

### Requirement: Plain-language action equivalence
The system SHALL validate requested, lower-court, and Supreme Court actions against typed canonical action slots while accepting reviewed ordinary-language equivalents. Every action sentence MUST identify its actor and MUST preserve the supported action, object, negation, and interim or final effect.

#### Scenario: Court vacates a judgment
- **WHEN** a typed Court-action claim says the Supreme Court vacated a judgment and prose says the Supreme Court cancelled that judgment
- **THEN** the prose is accepted as the same canonical action if all other grounding checks pass

#### Scenario: Court sends a case back
- **WHEN** a typed Court-action claim says the Supreme Court remanded the case and prose says the Supreme Court sent the case back to the lower court
- **THEN** the prose is accepted as the same canonical action

#### Scenario: Actor changes during translation
- **WHEN** prose assigns a party's request or lower-court action to the Supreme Court
- **THEN** publication is rejected regardless of readability

### Requirement: Opinion-aware status transition
The system MUST publish a new order-issued or decided status only as part of an atomic case revision whose prose is regenerated and validated from every required current argument session and accepted official order or opinion. A metadata-only update MUST NOT change legal status or maturity when existing prose describes the matter as awaiting a Court decision.

#### Scenario: Opinion follows oral argument
- **WHEN** the Court publishes an accepted opinion for an argued case
- **THEN** the pipeline parses and analyzes the opinion, rewrites the whole-case guide to explain the supported result, and advances status, maturity, disposition metadata, and prose together

#### Scenario: Opinion rewrite fails
- **WHEN** opinion-aware planning, writing, or validation fails
- **THEN** the complete prior case remains active and the new official activity is retained only as sanitized pending work

#### Scenario: Corrected disposition URL
- **WHEN** an official disposition URL or date changes without changing legal status, maturity, or prose meaning
- **THEN** the system may update metadata without model generation after all source and consistency checks pass

### Requirement: Bounded newest-first editorial backfill
The system SHALL maintain a versioned sanitized editorial-backfill cursor and SHALL select only the configured newest-first slice of cases that lack the target processor fingerprint. Cases outside the selected slice MUST NOT be represented as budget-exhausted pending work merely because a larger legacy corpus remains.

#### Scenario: Ten-case canary against a legacy corpus
- **WHEN** 1,710 legacy cases need the new processor and the rollout stage limit is ten
- **THEN** no more than ten backfill cases enter that cycle and the other 1,700 cases remain outside pending work

#### Scenario: Runtime ends inside a selected slice
- **WHEN** source, model, token, or runtime budget ends before every selected case is attempted
- **THEN** only selected unfinished cases are deferred and the cursor resumes without skipping or duplicating them

#### Scenario: Newest case repeatedly fails
- **WHEN** a selected newest case has a retryable model-output failure
- **THEN** its bounded retry scope does not prevent the backfill cursor from selecting the next eligible older case

#### Scenario: Processor contract changes
- **WHEN** the planner, prompt, reader-language resource, validation policy, model identity, or relevant configuration changes
- **THEN** the system starts a distinct backfill identity and does not treat briefs from the prior identity as migrated

### Requirement: Measured canary promotion
Editorial backfill SHALL begin with a publication-disabled fixed ten-case canary and MUST NOT advance to larger stages without a reviewed result. Advancement requires at least eight accepted improved rewrites, zero accepted factual/legal-status/actor/chronology/prediction errors, no degraded legacy page, and successful privacy and release validation.

#### Scenario: Canary meets threshold
- **WHEN** at least eight of ten fixed cases produce accepted improvements, all accepted cases are accurate, and manual review approves the exact candidate
- **THEN** the exact retained candidate is eligible for guarded promotion and the next backfill stage may increase to 25

#### Scenario: Canary misses threshold
- **WHEN** fewer than eight cases improve or any accepted case contains a factual, status, actor, chronology, or prediction error
- **THEN** the live release remains unchanged and rollout cannot advance

#### Scenario: Twenty-five-case stage succeeds
- **WHEN** a reviewed 25-case stage satisfies the same automated and manual quality requirements
- **THEN** the next stage may select up to 100 cases without overriding any runtime or model budget

### Requirement: Private writing boundary
Official source text, parsed text, section packets, detailed diagnostics, rejected prose, prompts, and raw model responses MUST remain in the permission-restricted run workspace and memory. Public state, logs, receipts, handoff metadata, and artifacts SHALL contain only existing allowlisted public content, fixed safe codes, sanitized case identifiers, aggregate canary results, and opaque fingerprints.

#### Scenario: Paragraph repair occurs
- **WHEN** the system sends rejected prose and a detailed correction instruction to the writer
- **THEN** neither value appears in logs, persisted retry state, cost receipts, generated-content state, or uploaded artifacts

#### Scenario: Failed canary is retained for review
- **WHEN** a publication-disabled canary produces a validated public candidate
- **THEN** only the privacy-scanned public site/state, opaque receipts, fixed handoff metadata, and sanitized aggregate review data are retained
