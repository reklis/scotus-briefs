# Evidence extraction

Extraction uses a strict JSON schema and exact character ranges into a private transcript revision. Unsupported observations fail closed. Deterministic post-processing adds high-confidence modality, routine, location, and sensitivity signals while retaining the model's source-linked observations.

The validator requires the evidence quote to exactly match the source range, rejects positive claims that lose negation, rejects unsupported injury claims, and prevents location normalization from inventing a DC quadrant. Raw values remain private alongside conservative normalized values.

Every extraction records model, schema, prompt, and vocabulary versions. Reprocessing the same transcript under the same version tuple returns the existing immutable observations; changing a version creates a new extraction revision.
