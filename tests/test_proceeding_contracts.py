from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from ragchew.proceedings.contracts import (
    ActionType,
    EvidenceKind,
    GovernmentActionStatus,
    GovernmentAuthority,
    Jurisdiction,
    OfficialSource,
    ParticipantRole,
    Proceeding,
    ProceedingEvidenceReference,
    ProceedingLifecycle,
    ProceedingObservation,
    ProceedingParticipant,
    ProceedingTranscriptSegment,
    ProceedingType,
    SourceAccessMethod,
    SpeakerIdentityBasis,
    StatementType,
    TranscriptSegmentStatus,
)

NOW = datetime(2026, 9, 1, 14, 0, tzinfo=UTC)


def source(**overrides: object) -> OfficialSource:
    values: dict[str, object] = {
        "source_id": "supreme_court",
        "authority": GovernmentAuthority.US_SUPREME_COURT,
        "jurisdiction": Jurisdiction.FEDERAL,
        "display_name": "Supreme Court of the United States",
        "official_index_url": "https://www.supremecourt.gov/oral_arguments/",
        "adapter": "supreme_court",
        "discovery_method": SourceAccessMethod.OFFICIAL_PAGE,
        "media_method": SourceAccessMethod.DOWNLOADABLE_FILE,
        "access_basis": "Approved official download access",
        "access_reviewed_at": NOW,
        "access_reviewed_by": "operator@example.test",
        "access_review_expires_at": NOW + timedelta(days=365),
        "allowed_hosts": ("www.supremecourt.gov",),
        "poll_interval_seconds": 900,
        "expected_schedule": "Published term calendar",
        "enabled": True,
    }
    values.update(overrides)
    return OfficialSource.model_validate(values)


def test_enabled_source_requires_access_review() -> None:
    assert source().enabled
    with pytest.raises(ValidationError, match="approved access basis"):
        source(access_basis=None)


def test_source_rejects_credentials_and_duplicate_hosts() -> None:
    with pytest.raises(ValidationError, match="without credentials"):
        source(official_index_url="https://user:secret@www.supremecourt.gov/")
    with pytest.raises(ValidationError, match="unique"):
        source(allowed_hosts=("www.supremecourt.gov", "www.supremecourt.gov"))


def test_proceeding_timestamps_must_be_utc_and_ordered() -> None:
    base = {
        "source_id": "supreme_court",
        "authority": "us_supreme_court",
        "jurisdiction": "federal",
        "external_id": "24-123",
        "proceeding_type": ProceedingType.ORAL_ARGUMENT,
        "title": "Example v. Example",
        "official_url": "https://www.supremecourt.gov/docket/docketfiles/html/public/24-123.html",
        "lifecycle": ProceedingLifecycle.SCHEDULED,
        "scheduled_start_at": NOW,
        "scheduled_end_at": NOW + timedelta(hours=1),
        "discovered_at": NOW,
        "updated_at": NOW,
    }
    assert Proceeding.model_validate(base).external_id == "24-123"
    with pytest.raises(ValidationError, match="timezone-aware UTC"):
        Proceeding.model_validate({**base, "scheduled_start_at": datetime(2026, 9, 1, 14)})
    with pytest.raises(ValidationError, match="scheduled_end_at"):
        Proceeding.model_validate({**base, "scheduled_end_at": NOW - timedelta(minutes=1)})


def test_private_witness_cannot_have_public_name() -> None:
    with pytest.raises(ValidationError, match="public officials"):
        ProceedingParticipant(
            proceeding_id=uuid4(),
            display_name_private="Jane Example",
            public_name="Jane Example",
            role=ParticipantRole.PRIVATE_WITNESS,
            identity_basis=SpeakerIdentityBasis.EXPLICIT_INTRODUCTION,
            identity_evidence_ids=(uuid4(),),
        )


def test_gap_segment_cannot_invent_text() -> None:
    with pytest.raises(ValidationError, match="cannot contain text"):
        ProceedingTranscriptSegment(
            transcript_revision_id=uuid4(),
            media_asset_id=uuid4(),
            sequence=1,
            start_ms=300_000,
            end_ms=420_000,
            status=TranscriptSegmentStatus.GAP,
            text_private="invented bridge",
        )


def test_spoken_evidence_requires_ordered_time_range() -> None:
    with pytest.raises(ValidationError, match="ordered time range"):
        ProceedingEvidenceReference(
            evidence_kind=EvidenceKind.SPOKEN_MEDIA,
            source_id=uuid4(),
            start_ms=10,
            end_ms=5,
        )


def test_vote_totals_require_vote_record_evidence() -> None:
    evidence = ProceedingEvidenceReference(
        evidence_kind=EvidenceKind.SPOKEN_MEDIA,
        source_id=uuid4(),
        start_ms=1_000,
        end_ms=2_000,
    )
    with pytest.raises(ValidationError, match="vote-record evidence"):
        ProceedingObservation(
            extraction_revision_id=uuid4(),
            proceeding_id=uuid4(),
            jurisdiction=Jurisdiction.FEDERAL,
            authority=GovernmentAuthority.US_HOUSE,
            body="U.S. House",
            statement_type=StatementType.DEBATE,
            action_type=ActionType.VOTE,
            action_status=GovernmentActionStatus.PASSED_ONE_CHAMBER,
            raw_value_private="The tally is 220 to 210.",
            vote_yes=220,
            vote_no=210,
            confidence=0.9,
            occurred_at=NOW,
            evidence=(evidence,),
        )
