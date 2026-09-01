# Public-launch acceptance thresholds

Public launch is fail-closed. Every threshold in `config/launch-thresholds.yaml` must pass over at least seven consecutive private-preview days. A critical privacy, grounding, cross-location merge, negation, or publication-policy failure resets the run after remediation.

## Evaluation population

Review at least 200 randomly sampled calls, every candidate that could have become public, at least 30 publishable incidents, 50 calls with locations, and 30 negation-sensitive calls. Compare retained audio with transcript, observations, incident history, policy decision, approved claims, and generated revision. Label reviewers' expected results before inspecting generated public prose.

## Capture completeness

A healthy minute has a timely heartbeat and at least 10 valid control messages per second. At least 99% of expected operational minutes must be control-healthy and 99.5% of heartbeat intervals must arrive. At least 95% of decoded, clear, allowed, in-window voice grants must produce finalized artifacts. No encrypted or unselected audio and no acknowledged/unacknowledged spool item may be silently lost. Planned maintenance is excluded only when documented before the interval.

## Transcription and evidence extraction

Reviewed speech must have WER no greater than 25% and at least 90% accuracy for units, street/block, quadrant, incident type, and operational status. False text on silent or unintelligible audio must remain below 1%. Location observations require at least 98% precision and 90% recall; guessed quadrants are never allowed. Negation recall must be 100%, with zero polarity reversals.

## Incident grouping

Against reviewer-labeled observation pairs, grouping must reach at least 98% precision and 90% recall. Cross-location false merges are prohibited. Duplicate logical incidents must remain below 2%. Corrections, cancellations, contradictions, and cross-hour updates must retain one append-only incident history.

## Privacy and publication policy

Mandatory-sensitive-category recall must be 100%. Across all generated previews and public-boundary scans, there may be zero names, exact residential units, patient details, source-radio identifiers, audio/object locations, transcripts, private observations, credentials, suppressed incidents, or suppressed counts. There may be zero public stories for ineligible incidents.

## Factual grounding

Every factual title, summary, status, and timeline element must map to an approved claim and preserve its certainty. Unsupported causes, casualties, names, outcomes, or stronger confirmation are prohibited. A failed generation or hourly cycle must leave the previous projection unchanged.

## Decision record

Record numerator, denominator, failures, reviewer notes, software/model/configuration versions, and evaluation query for each metric. Launch approval requires a signed validation report showing every gate passed. Averages cannot offset a zero-tolerance failure.
