## Why

The existing reader-guide writer and validator have produced zero trustworthy canary passes, while recent probes show that exact local GPT-OSS can return schema-valid, concise plain-language prose within safe Spark resource limits. The product only needs a short Citizen’s Guide to each case, so generation should use a narrower field-specific prompt rather than another general legal brief or a second-model publication gate.

## What Changes

- Retarget the rejected Mistral candidate change to exact local `ragchew-gpt-oss:120b-32k`, pinned to its reviewed immutable Ollama digest and bounded 32,768-token context.
- Replace the general compact-writer instruction with a versioned Citizen’s Guide prompt for four planner-controlled sections: what the case is about, what the sides say, what the Supreme Court did, and why it matters.
- Give each section only its approved claims and applicable canonical action slots; deterministic code continues to own identity, headings, order, citations, claim IDs, and publication eligibility.
- Require a concise guide of no more than 180 words, one or two short sentences per section, ordinary language, and immediate explanation of any unavoidable legal term.
- Preserve actor, action, object, court level, attribution, polarity, timing, request-versus-ruling, and interim-versus-final distinctions while allowing omission of nonessential detail.
- Keep deterministic privacy, grounding, schema, action-slot, status, release, and fail-closed checks authoritative. Granite or another reviewer model is not part of the publication path.
- Qualify the exact prompt and model with synthetic role-sensitive cases and a publication-disabled measured canary before any production promotion.

## Capabilities

### New Capabilities
- `scotus-mistral-model-qualification` (retargeted historical identifier): Exact local GPT-OSS identity and resource qualification, field-specific Citizen’s Guide generation, and publication-disabled measured rollout. The capability path retains its original name because this existing change was retargeted rather than recreated.

### Modified Capabilities

None.

## Impact

This affects SCOTUS model configuration and allowlists, prompt/request construction, canonical action-slot packets, processor fingerprints, Ollama preflight, draft validation diagnostics, qualification fixtures, workflow policy, tests, and model/security/operations documentation. It adds no hosted provider, credential, reviewer model, or automated model pull; private Court material remains on the protected Spark host.
