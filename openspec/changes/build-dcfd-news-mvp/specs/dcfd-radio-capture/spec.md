## ADDED Requirements

### Requirement: Selected clear DCFD calls are captured as discrete artifacts
The edge receiver SHALL record finalized audio artifacts only for configured unencrypted DCFD talkgroups available within its configured RF window, and SHALL associate each artifact with radio metadata.

#### Scenario: Selected clear call completes
- **WHEN** a clear transmission on an allowed DCFD talkgroup is received and finalized
- **THEN** the edge creates one call artifact containing audio, talkgroup, UTC timing, frequency, receiver identity, and available source-radio and decoder metadata

#### Scenario: Encrypted call is observed
- **WHEN** the control channel identifies a call as encrypted
- **THEN** the edge does not forward audio from that call for transcription

#### Scenario: Unselected talkgroup is active
- **WHEN** a transmission occurs on a talkgroup outside the configured allowlist
- **THEN** the edge does not create a forwarded call artifact for that transmission

### Requirement: Calls are forwarded at least once through a durable spool
The edge SHALL persist each finalized call in a local spool before upload and SHALL retain it until the cluster acknowledges durable ingestion.

#### Scenario: Cluster is temporarily unavailable
- **WHEN** upload or acknowledgement fails
- **THEN** the edge retains the call with its original capture identifier and retries without silently dropping it

#### Scenario: Acknowledgement is received
- **WHEN** the cluster acknowledges that a call is durably ingested
- **THEN** the edge marks the spool entry acknowledged and deletes it only according to the configured edge grace period

#### Scenario: Spool approaches capacity
- **WHEN** unacknowledged calls consume the configured spool capacity threshold
- **THEN** the edge raises a visible health alert and applies configured backpressure rather than silently deleting unacknowledged calls

### Requirement: Capture delivery is idempotent
The edge SHALL assign a deterministic identifier and content digest to each captured call so retries can be recognized as the same call.

#### Scenario: A call is retried after a lost acknowledgement
- **WHEN** the edge resubmits an already ingested capture identifier and matching digest
- **THEN** the cluster can acknowledge the existing call without creating a duplicate logical call

#### Scenario: Identifier is reused with different content
- **WHEN** a capture identifier is submitted with a digest different from the previously acknowledged content
- **THEN** the edge records the ingestion rejection and retains the conflicting spool item for operator review

### Requirement: Edge health is observable
The edge SHALL emit periodic receiver and forwarding health information independently of voice-call activity.

#### Scenario: Radio traffic is quiet but capture is healthy
- **WHEN** no selected voice call occurs during a health interval but control-channel decoding and heartbeats continue
- **THEN** the reported state distinguishes a healthy quiet receiver from a failed receiver

#### Scenario: Forwarding backlog grows
- **WHEN** the oldest unacknowledged spool item exceeds the configured age threshold
- **THEN** the edge reports degraded delivery health with spool depth and oldest-item age

#### Scenario: Capture host is resource constrained
- **WHEN** dropped samples, low disk, excessive temperature, or clock drift crosses a configured threshold
- **THEN** the edge emits a degraded or failed health state identifying the affected measurement

### Requirement: Single-receiver limitations are explicit
The edge SHALL report its configured RF coverage and SHALL NOT claim to capture calls outside that coverage.

#### Scenario: Voice assignment falls outside the RF window
- **WHEN** the control channel grants a selected talkgroup call on a frequency the receiver cannot cover
- **THEN** the edge records a missed-or-out-of-range metric when detectable and does not represent the call as captured
