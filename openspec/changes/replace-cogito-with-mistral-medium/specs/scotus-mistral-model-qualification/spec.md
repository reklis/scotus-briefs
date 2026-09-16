## ADDED Requirements

### Requirement: Reviewed exact local GPT-OSS identity
The system SHALL use `ragchew-gpt-oss:120b-32k` for SCOTUS extraction or Citizen’s Guide generation only after its upstream provenance, Apache-2.0 license, derived Modelfile, 32,768-token context ceiling, resource fit, full native Ollama digest, and owner eligibility are reviewed and recorded, and SHALL bind the exact tag and digest to processor and request fingerprints.

#### Scenario: Reviewed exact artifact is installed
- **WHEN** protected SCOTUS processing preflights loopback Ollama
- **THEN** processing proceeds only if one installed entry matches `ragchew-gpt-oss:120b-32k` and digest `820a68f9c4f7253846f5d82d225bc45ca93faa8cfe2a1009bff6efb15d662863` and its reviewed context ceiling and request controls are active

#### Scenario: Identity or operating envelope differs
- **WHEN** the exact tag is absent, resolves to different content, drifts before or after a completion, exceeds the reviewed context ceiling, or cannot be validated
- **THEN** the system fails closed without Court retrieval, private-evidence model use, fallback selection, or publication

#### Scenario: Protected automation encounters a missing model
- **WHEN** a workflow cannot find the reviewed local artifact
- **THEN** it SHALL NOT pull a model, select `latest`, use the native-context GPT-OSS tag, contact a hosted provider, or substitute another installed model

### Requirement: Bounded plain-language Citizen’s Guide
The system MUST use a versioned strict-schema prompt profile with an explicit low GPT-OSS reasoning level that produces only the applicable planner-controlled Citizen’s Guide fields, limits the complete guide to 180 words, limits each field to one or two short sentences, and uses ordinary language with any unavoidable specialist term explained immediately. Only final schema content may be parsed or retained; model reasoning MUST NOT be logged, persisted, included in diagnostics or receipts, or exposed publicly.

#### Scenario: Decided case has sufficient approved evidence
- **WHEN** the planner provides approved issue, party-position, Supreme Court outcome, and impact evidence
- **THEN** GPT-OSS returns concise schema-valid text for what the case is about, what the sides say, what the Supreme Court did, and why it matters without lawyer names, citations in prose, justice-by-justice detail, unnecessary procedural history, or prediction

#### Scenario: Outcome evidence is unavailable
- **WHEN** the approved evidence does not establish a Supreme Court outcome
- **THEN** the writer uses only a planner-approved status statement or omits the inapplicable outcome field according to the existing maturity contract and does not infer who won or what the Court will do

#### Scenario: Bounded low reasoning returns final content
- **WHEN** the exact GPT-OSS request runs with the reviewed low reasoning level
- **THEN** reasoning use remains within existing context, output-token, timeout, retry, call, and runtime bounds and only nonempty final strict-schema content enters parsing

#### Scenario: Reasoning or final-content protocol fails
- **WHEN** final content is empty, reasoning appears in final content or any retained artifact, the schema is malformed, or reasoning consumes the output allowance
- **THEN** the request fails closed without fallback, Court-evidence advancement, candidate retention, or publication

#### Scenario: Output is verbose or jargon-heavy
- **WHEN** generated output exceeds the field or total word bounds, includes unexplained legal jargon, or adds excluded detail
- **THEN** the draft is rejected or repaired within existing bounded retry policy and remains publication-disabled

### Requirement: Field-specific evidence and action boundaries
The system SHALL construct each model-writable field from only the approved claims and canonical action slots assigned to that field, while deterministic code retains control of case identity, title, headings, section order, sources, citations, claim IDs, action-slot IDs, legal status, and publication eligibility.

#### Scenario: Writer receives party positions
- **WHEN** GPT-OSS writes the sides field
- **THEN** its packet contains only attributed party positions and requests and the result uses party-language such as argues, says, asks, or wants rather than presenting those positions as court holdings

#### Scenario: Writer receives court actions
- **WHEN** GPT-OSS writes a history, status, or outcome field
- **THEN** every supplied action slot identifies the acting court and the writer preserves that court, action, object, polarity, timing, and interim or final effect

#### Scenario: Evidence belongs to another field
- **WHEN** an approved fact or action slot is not assigned to the current field
- **THEN** the model cannot cite, select, move, or synthesize that fact into the field

#### Scenario: Field evidence is insufficient
- **WHEN** no approved claim or canonical action slot supports a material statement for a field
- **THEN** deterministic planning supplies an approved fallback or omission and the model does not improvise content

### Requirement: Role-explicit GPT-OSS request profile
The system MUST instruct GPT-OSS to name the relevant actor, avoid ambiguous cross-sentence references, preserve attribution and uncertainty, distinguish seeking or receiving an order from issuing one, and reserve court-action verbs for the court and action supplied by the canonical slot.

#### Scenario: Party asks for relief
- **WHEN** the canonical slot records that a party requested reversal, removal, a stay, or another result
- **THEN** generated prose states that the party asked or argued for that result and does not state that a court granted it

#### Scenario: Lower court and Supreme Court acted differently
- **WHEN** the packet contains distinct lower-court and Supreme Court slots
- **THEN** generated prose identifies each acting court and does not attribute either court’s ruling, remedy, or procedural effect to the other

#### Scenario: Agency receives a court order
- **WHEN** a canonical slot states that an agency sought or received an order issued by a court
- **THEN** generated prose does not state that the agency issued the order

#### Scenario: Request semantics change
- **WHEN** the prompt text, schema, reasoning level, model tag, digest, context ceiling, or generation controls change semantically
- **THEN** the request-profile version and processor/request fingerprints change and prior qualification does not authorize the new profile

### Requirement: Canonical action validation with private diagnostics
The system SHALL hard-reject a generated action statement only for a demonstrated conflict with its canonical field slot or omission of a required slot, while treating an unrecognized lexical paraphrase without a demonstrated conflict as a private diagnostic rather than proof of factual error.

#### Scenario: Generated action contradicts its slot
- **WHEN** prose changes the actor, action, object, court level, polarity, status, timing, request-versus-ruling role, or interim-versus-final effect established by the canonical slot
- **THEN** validation rejects the draft with a process-local diagnostic identifying the field, detected tuple, expected slot, and contradiction reason

#### Scenario: Required outcome is omitted
- **WHEN** a decided-case plan requires a Supreme Court outcome slot and the generated outcome field does not express it
- **THEN** validation rejects the draft with a process-local omission diagnostic

#### Scenario: Faithful wording uses an unrecognized synonym
- **WHEN** prose remains consistent with the canonical slot but a regex or lexical classifier cannot map an ordinary-language synonym confidently
- **THEN** the system records an ambiguous lexical diagnostic without classifying the prose as contradicted solely for that mismatch

#### Scenario: Diagnostic lifecycle completes
- **WHEN** generation, repair, validation, rejection, or process shutdown completes
- **THEN** source text, prompts, responses, model reasoning, rejected prose, and detailed diagnostics are absent from logs, durable artifacts, generated state, and public files

### Requirement: Existing hard safety gates remain authoritative
Adopting GPT-OSS and recalibrating action diagnostics SHALL NOT relax source authorization, claim grounding, unknown-reference, schema, polarity, chronology, legal-status, unsupported prediction, process-disclosure, severe-bound, privacy, transient-workspace, budget, static-release, or publication validation.

#### Scenario: Readable prose adds an unsupported fact
- **WHEN** GPT-OSS produces fluent plain language that is not grounded in the field’s approved claims or changes a canonical fact
- **THEN** hard validation rejects the output and leaves the prior active case and release unchanged

#### Scenario: Private processing ends
- **WHEN** probing, extraction, generation, repair, validation, rejection, or failure ends
- **THEN** private Court material, model reasoning, and final model content are removed according to existing transient-workspace and no-retention policy

#### Scenario: Reviewer model is unavailable
- **WHEN** no Granite, MiniCheck, or other reviewer model is installed or running
- **THEN** normal processing behavior is unchanged because no reviewer model participates in factual acceptance or publication

### Requirement: Publication-disabled GPT-OSS qualification and approval
The system SHALL qualify the exact GPT-OSS prompt and artifact with role-sensitive synthetic fixtures, a fixture-backed end-to-end run, and a fixed publication-disabled ten-case canary before promotion, and SHALL require the existing improvement, correctness, privacy, release, and explicit-review thresholds.

#### Scenario: Synthetic qualification passes
- **WHEN** cold and warm low-reasoning strict extraction and Citizen’s Guide probes return nonempty final schema content, retain no reasoning, preserve labeled positive paraphrases, and reject labeled actor, action, object, court, polarity, status, and order-issuer counterexamples within token, time, memory, and cleanup bounds
- **THEN** the exact candidate may advance to fixture-backed publication-disabled qualification

#### Scenario: Canary qualifies but approval is pending
- **WHEN** at least eight of ten guides are hard-valid and manually judged improved, no accepted correctness error or legacy-page degradation exists, and privacy and release checks pass, but exact candidate-bound approval is absent
- **THEN** public promotion and larger stages remain rejected

#### Scenario: Canary does not qualify
- **WHEN** any required threshold, complete outcome accounting, exact identity, evidence binding, private cleanup, or release validation fails
- **THEN** the system retains only permitted sanitized metrics and opaque receipts, uploads no promotable candidate, and leaves the active release unchanged

#### Scenario: Exact candidate receives approval
- **WHEN** every qualification and review requirement passes and explicit approval identifies the exact model, digest, prompt profile, processor, parent, and candidate
- **THEN** only that candidate may be promoted before sequential measured 25-case and up-to-100-case stages
