from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from ragchew.scotus.contracts import (
    AdvocateRole,
    ArgumentSession,
    ArgumentStatus,
    DocumentRevisionStatus,
    LegalCertainty,
    LegalEvidenceRange,
    LegalObservation,
    LegalObservationType,
    LegalStatus,
    ScotusDocumentKind,
    ScotusDocumentRevision,
    SpeakerIdentityBasis,
    SpeakerKind,
    TranscriptTurn,
)

NOW = datetime(2026, 8, 28, 2, tzinfo=UTC)


def evidence(kind: ScotusDocumentKind) -> LegalEvidenceRange:
    return LegalEvidenceRange(
        document_revision_id=uuid4(),
        document_kind=kind,
        start_file_page=5,
        start_line=1,
        end_file_page=5,
        end_line=3,
        quote_private="The judgment should be reversed.",
    )


def test_ready_argument_requires_transcript_document() -> None:
    with pytest.raises(ValidationError, match="requires a transcript"):
        ArgumentSession(
            case_id=uuid4(),
            term="2025",
            session_key="25-466:2026-04-20:1",
            argument_date=NOW,
            status=ArgumentStatus.TRANSCRIPT_READY,
            official_detail_url="https://www.supremecourt.gov/oral_arguments/audio/2025/25-466",
            discovered_at=NOW,
            updated_at=NOW,
        )


def test_ready_document_requires_private_object_provenance() -> None:
    with pytest.raises(ValidationError, match="ready document"):
        ScotusDocumentRevision(
            case_id=uuid4(),
            argument_id=uuid4(),
            kind=ScotusDocumentKind.TRANSCRIPT,
            external_id="25-466-transcript",
            revision_number=1,
            official_url="https://www.supremecourt.gov/transcript.pdf",
            status=DocumentRevisionStatus.READY,
            content_type="application/pdf",
            observed_at=NOW,
        )


def test_named_transcript_speaker_requires_official_identity_basis() -> None:
    with pytest.raises(ValidationError, match="affirmative identity"):
        TranscriptTurn(
            parse_revision_id=uuid4(),
            document_revision_id=uuid4(),
            sequence=0,
            start_file_page=5,
            start_line=1,
            end_file_page=5,
            end_line=3,
            speaker_label_private="JUSTICE KAGAN",
            speaker_name="Justice Kagan",
            speaker_kind=SpeakerKind.JUSTICE,
            identity_basis=SpeakerIdentityBasis.ANONYMOUS,
            text_private="Is that rule consistent with the statute?",
            confidence=1,
        )


def test_advocate_role_requires_advocate_speaker_kind() -> None:
    with pytest.raises(ValidationError, match="advocate speaker"):
        TranscriptTurn(
            parse_revision_id=uuid4(),
            document_revision_id=uuid4(),
            sequence=0,
            start_file_page=5,
            start_line=1,
            end_file_page=5,
            end_line=3,
            speaker_kind=SpeakerKind.JUSTICE,
            advocate_role=AdvocateRole.PETITIONER,
            text_private="May it please the Court.",
            confidence=1,
        )


def test_transcript_cannot_establish_holding() -> None:
    with pytest.raises(ValidationError, match="order/opinion evidence"):
        LegalObservation(
            extraction_revision_id=uuid4(),
            case_id=uuid4(),
            argument_id=uuid4(),
            observation_type=LegalObservationType.HOLDING,
            legal_status=LegalStatus.COURT_HELD,
            certainty=LegalCertainty.DIRECT,
            raw_value_private="The Court held for petitioner.",
            confidence=0.9,
            evidence=(evidence(ScotusDocumentKind.TRANSCRIPT),),
        )


def test_advocate_contention_requires_attribution() -> None:
    with pytest.raises(ValidationError, match="requires attribution"):
        LegalObservation(
            extraction_revision_id=uuid4(),
            case_id=uuid4(),
            argument_id=uuid4(),
            observation_type=LegalObservationType.ADVOCATE_CONTENTION,
            legal_status=LegalStatus.ASSERTED,
            certainty=LegalCertainty.ATTRIBUTED,
            raw_value_private="The statute does not authorize the agency action.",
            confidence=0.9,
            evidence=(evidence(ScotusDocumentKind.TRANSCRIPT),),
        )


def test_official_opinion_can_support_holding() -> None:
    observation = LegalObservation(
        extraction_revision_id=uuid4(),
        case_id=uuid4(),
        observation_type=LegalObservationType.HOLDING,
        legal_status=LegalStatus.COURT_HELD,
        certainty=LegalCertainty.DIRECT,
        raw_value_private="The statute does not authorize the challenged action.",
        confidence=1,
        evidence=(evidence(ScotusDocumentKind.OPINION),),
    )
    assert observation.legal_status is LegalStatus.COURT_HELD
