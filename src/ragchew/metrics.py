"""Low-cardinality operational metrics with no transcript or private labels."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

INGESTION_TOTAL = Counter(
    "ragchew_ingestion_total", "Capture ingestion outcomes", ("outcome",)
)
INGESTION_LAG = Histogram(
    "ragchew_ingestion_lag_seconds", "Delay from call start to manifest ingestion"
)
OBJECT_FAILURES = Counter(
    "ragchew_object_failures_total", "Private object operation failures", ("operation",)
)
HEARTBEAT_TIMESTAMP = Gauge(
    "ragchew_receiver_heartbeat_timestamp_seconds",
    "Most recent edge heartbeat by configured receiver",
    ("receiver",),
)
CONTROL_ACTIVITY = Gauge(
    "ragchew_control_messages_per_minute",
    "Control-channel activity reported by the edge",
    ("receiver",),
)
EDGE_SPOOL_DEPTH = Gauge(
    "ragchew_edge_spool_depth", "Unacknowledged edge spool entries", ("receiver",)
)
EDGE_FREE_DISK = Gauge(
    "ragchew_edge_free_disk_bytes", "Free bytes on the edge spool volume", ("receiver",)
)
EDGE_CLOCK_OFFSET = Gauge(
    "ragchew_edge_clock_offset_seconds", "Reported edge clock offset", ("receiver",)
)
EDGE_DROPPED_SAMPLES = Gauge(
    "ragchew_edge_dropped_samples", "Reported SDR dropped samples", ("receiver",)
)
JOB_OUTCOMES = Counter(
    "ragchew_job_outcomes_total", "Worker stage outcomes", ("stage", "outcome")
)
JOB_BACKLOG = Gauge(
    "ragchew_job_backlog", "Pending, retryable, or expired-lease jobs", ("stage",)
)
JOB_DURATION = Histogram(
    "ragchew_job_duration_seconds", "Worker stage processing duration", ("stage",)
)
STT_DURATION = Histogram("ragchew_stt_duration_seconds", "Private transcription duration")
EXTRACTION_FAILURES = Counter(
    "ragchew_extraction_failures_total", "Schema or evidence extraction failures"
)
INCIDENT_STATE_EVENTS = Counter(
    "ragchew_incident_state_events_total", "Correlated incident state outcomes", ("state",)
)
POLICY_DECISIONS = Counter(
    "ragchew_policy_decisions_total", "Publication policy outcomes", ("outcome",)
)
PUBLICATION_OUTCOMES = Counter(
    "ragchew_publication_outcomes_total", "Hourly publication outcomes", ("outcome",)
)
LAST_PUBLICATION_TIMESTAMP = Gauge(
    "ragchew_last_publication_timestamp_seconds", "Last successful projection activation"
)

SCOTUS_SOURCE_LAST_SUCCESS = Gauge(
    "ragchew_scotus_source_last_success_timestamp_seconds",
    "Last successful reviewed Supreme Court source poll",
)
SCOTUS_TRANSCRIPT_WAIT_AGE = Gauge(
    "ragchew_scotus_transcript_wait_age_seconds",
    "Age of the oldest argument waiting for an official transcript",
)
SCOTUS_DOCUMENT_OUTCOMES = Counter(
    "ragchew_scotus_document_outcomes_total",
    "Official Court document collection outcomes",
    ("kind", "outcome"),
)
SCOTUS_PARSER_OUTCOMES = Counter(
    "ragchew_scotus_parser_outcomes_total",
    "Official transcript parser outcomes",
    ("outcome",),
)
SCOTUS_EXTRACTION_OUTCOMES = Counter(
    "ragchew_scotus_extraction_outcomes_total",
    "Legal observation extraction outcomes",
    ("outcome",),
)
SCOTUS_CASE_STATE_EVENTS = Counter(
    "ragchew_scotus_case_state_events_total",
    "Correlated Supreme Court case state outcomes",
    ("state",),
)
SCOTUS_BRIEF_POLICY_OUTCOMES = Counter(
    "ragchew_scotus_brief_policy_outcomes_total",
    "Legal brief policy outcomes",
    ("outcome",),
)
SCOTUS_CORRECTIONS = Counter(
    "ragchew_scotus_corrections_total",
    "Visible brief corrections and retractions",
    ("kind",),
)
SCOTUS_LAST_PUBLICATION = Gauge(
    "ragchew_scotus_last_publication_timestamp_seconds",
    "Last successful SCOTUS projection activation",
)
SCOTUS_RETENTION_OUTCOMES = Counter(
    "ragchew_scotus_retention_outcomes_total",
    "Private Court document and extracted text retention outcomes",
    ("kind", "outcome"),
)
