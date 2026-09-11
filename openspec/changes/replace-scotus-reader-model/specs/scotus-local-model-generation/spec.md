## ADDED Requirements

### Requirement: Exact local Cogito model identity
The system SHALL use only loopback Ollama model `cogito:70b` with reviewed content digest `8f2632d0faa422ff60435bc0095575d032a8b4a0f728df034d90ea654ffb60bb` for SCOTUS extraction and reader-guide generation, and SHALL include the configured model identity in processor and request fingerprints.

#### Scenario: Exact installed model is available
- **WHEN** a protected SCOTUS build preflights the loopback Ollama inventory
- **THEN** it proceeds only if one installed entry matches both the configured `cogito:70b` tag and the full reviewed digest

#### Scenario: Tag or digest differs
- **WHEN** the configured tag is absent, resolves to different content, or the inventory cannot be validated
- **THEN** the build fails before Court retrieval or model completion and does not fall back to another installed model

#### Scenario: Processor identity changes
- **WHEN** the reviewed generation model changes from `qwen3.8:27b` to exact `cogito:70b` content
- **THEN** processor and request fingerprints differ while unchanged prompt and validation-policy versions remain identifiable

### Requirement: Production-protocol compatibility qualification
The system MUST qualify the replacement through the same loopback OpenAI-compatible strict JSON-schema protocol and bounded non-thinking request behavior used by production before live Court evidence is submitted.

#### Scenario: Synthetic grounded probe passes
- **WHEN** a repository-authored synthetic record states an exact Court actor, action, object, and no future winner
- **THEN** `cogito:70b` returns schema-valid bounded JSON with the supported values and no prediction within the configured transport, token, and time limits

#### Scenario: Compatibility probe fails
- **WHEN** the response is unavailable, malformed, schema-invalid, unsupported, predictive, or exceeds a configured bound
- **THEN** qualification fails without Court retrieval, fallback model use, publication, or processor approval

### Requirement: Existing correctness and privacy gates remain authoritative
Replacing the model SHALL NOT relax source authorization, grounding, actor/action, chronology, legal-status, prediction, process-disclosure, severe-bound, privacy, transient-workspace, cost, or publication validation.

#### Scenario: Larger model returns unsupported prose
- **WHEN** Cogito produces fluent prose that changes official meaning or lacks approved support
- **THEN** the existing hard validator rejects it and retains the active public case unchanged

#### Scenario: Editorial-only finding occurs
- **WHEN** a Cogito candidate passes every hard gate but triggers a configured reader-language, preferred-length, readability, repetition, terminology, or nonmaterial section-focus finding
- **THEN** the candidate remains reviewable with only fixed sanitized warning counts

#### Scenario: Private material lifecycle
- **WHEN** extraction, generation, repair, or rejection completes or fails
- **THEN** official source text, prompts, responses, diagnostics, and rejected prose remain transient and are absent from logs, artifacts, generated state, and public files

### Requirement: Comparable publication-disabled replacement canary
The system SHALL begin the new processor at a protected publication-disabled ten-case canary and SHALL reuse the rejected recalibrated manifest in the same order when all ten cases remain safely reconstructable.

#### Scenario: Prior manifest remains eligible
- **WHEN** the replacement processor starts `canary_10` and every prior manifest case is still available and reconstructable
- **THEN** the measured run uses exactly those ten case keys in the recorded order, reserves bounded slots for them, and keeps unrelated fresh activity explicit pending

#### Scenario: Prior manifest cannot be reconstructed
- **WHEN** any prior manifest case is missing, ambiguous, source-invalid before selection, or cannot be safely reconstructed
- **THEN** the canary fails closed instead of substituting another case

#### Scenario: Canary has hard-valid candidates
- **WHEN** one or more manifest cases produce hard-valid reader guides
- **THEN** the workflow retains only privacy-scanned candidate/review artifacts, remains publication-disabled, and requires side-by-side review of every candidate and warning against the active page and official-source meaning

#### Scenario: Canary has no hard-valid candidate
- **WHEN** all ten replacement-model rewrites fail hard
- **THEN** the workflow retains only a privacy-scanned sanitized manifest and aggregate report plus opaque receipts, records a rejected decision, and uploads no candidate site, candidate state, or promotion handoff

### Requirement: Approval remains necessary for rollout
The replacement model SHALL NOT authorize promotion by identity or probe success alone; measured rollout SHALL retain the existing eight-of-ten improvement threshold and zero-error, no-degradation, privacy, and release-validation requirements.

#### Scenario: Replacement canary does not qualify
- **WHEN** fewer than eight cases are accepted and judged improved, any accepted factual/status/actor/chronology/prediction error exists, any legacy page is degraded, or privacy/release validation fails
- **THEN** the live release remains unchanged and no 25-case or 100-case stage is authorized

#### Scenario: Replacement canary qualifies
- **WHEN** all qualification requirements pass and the exact candidate receives recorded reviewer approval
- **THEN** only that exact candidate may be promoted before sequential measured 25-case and up-to-100-case gates
