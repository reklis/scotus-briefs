# SCOTUS Legal Briefs acceptance thresholds

`config/scotus-launch-thresholds.yaml` is the machine-readable launch contract. Public enablement requires every zero-tolerance maximum to remain zero and every minimum/rate to pass over at least seven consecutive private-preview days and 20 manually reviewed representative cases.

## Source and transcript

At least 99% of scheduled polls must succeed, with zero duplicate case/session identities and zero collections from unapproved hosts, redirects, audio, or STT. Every accepted transcript must be complete. File page counts, page/line coordinates, and named speaker identity must be exact in the reviewed sample; ambiguous pages fail closed.

## Legal analysis

A justice's question must never become a holding, vote, or outcome prediction. Transcript-only evidence must never produce final Court action. Advocate contentions and disputed facts require attribution. Every quotation and published citation must have exact official evidence; citation recall must reach 95% while precision remains 100%.

Issue grouping requires at least 98% pairwise precision, 90% recall, and no cross-docket false merge. Consolidated cases and reargument are reviewed separately.

## Sensitivity and grounding

Minimization/suppression recall must be 100% for the configured sensitive classes, with no public leak and no publication of sealed/redacted details. Every factual/legal element must resolve to approved claims and official source ranges; unsupported procedural history is zero tolerance.

## Public boundary and operations

No copied PDF, full extracted transcript, parser artifact, prompt, private identifier, or credential may appear publicly. Failed publication cycles must leave the prior projection unchanged. Every public analysis page requires all legal-analysis disclosures and accessible official-source labels.

## Evidence required before launch

The validation report must record source poll counts, transcript publication latency, reviewed cases and page/line samples, speaker confusion matrix, citation and issue-grouping measurements, every policy denial/correction, fault-injection outcomes, retention/backup restoration, and public-role access tests. At least one newly published official transcript must traverse the deployed path when the Court calendar permits; otherwise live discovery remains unvalidated and launch stays disabled.
