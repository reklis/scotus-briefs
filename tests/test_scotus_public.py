from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from ragchew.scotus.briefs import CaseArgumentSession
from ragchew.scotus.contracts import (
    BriefArgumentAnalysis,
    BriefMaturity,
    BriefSection,
    LegalBriefRevision,
    LegalCertainty,
    LegalObservationType,
    LegalStatus,
    ScotusApprovedClaim,
    ScotusCaseStatus,
)
from ragchew.scotus.public import (
    create_scotus_public_app,
    latest_court_document_date,
    public_case_path,
    sort_cases,
)
from ragchew.scotus.public_contracts import (
    PublicBriefRevisionSummary,
    PublicCaseHistoryEvent,
    PublicSourceLink,
    public_case_slug,
)
from ragchew.scotus.publishing import (
    InMemoryScotusProjectionStore,
    build_public_case,
)

NOW = datetime(2026, 8, 28, 2, tzinfo=UTC)
CASE_ID = uuid4()
ARGUMENT_ID = uuid4()


def claim(
    value: str,
    observation_type: LegalObservationType,
    *,
    status: LegalStatus = LegalStatus.DESCRIBED,
) -> ScotusApprovedClaim:
    return ScotusApprovedClaim(
        case_id=CASE_ID,
        argument_id=ARGUMENT_ID,
        observation_type=observation_type,
        legal_status=status,
        certainty=LegalCertainty.ATTRIBUTED,
        public_value=value,
        official_url="https://www.supremecourt.gov/oral_arguments/transcript.pdf",
        public_source_label="Official Transcript",
        page_label="file page 5, lines 1-3",
        source_observation_ids=(uuid4(),),
        approved_at=NOW,
        policy_version="test-v1",
    )


def public_case(*, correction: bool = False):  # type: ignore[no-untyped-def]
    claims = (
        claim(
            "Whether the agency exceeded its statutory authority.",
            LegalObservationType.QUESTION_PRESENTED,
        ),
        claim(
            "Counsel for petitioner argued that the statute does not authorize the action.",
            LegalObservationType.ADVOCATE_CONTENTION,
            status=LegalStatus.ASSERTED,
        ),
        claim(
            "Justice Kagan asked whether the rule fit the statutory text.",
            LegalObservationType.JUSTICE_QUESTION,
            status=LegalStatus.QUESTIONED,
        ),
    )
    ids = tuple(item.claim_id for item in claims)
    revision = LegalBriefRevision(
        brief_id=uuid4(),
        case_id=CASE_ID,
        argument_id=ARGUMENT_ID,
        revision_number=2 if correction else 1,
        maturity=BriefMaturity.CORRECTED if correction else BriefMaturity.OFFICIAL_TRANSCRIPT,
        title="Did the agency have the power to act?",
        title_claim_ids=(ids[0],),
        dek="The two sides disagree about the power Congress gave the agency.",
        dek_claim_ids=ids[:2],
        sections=(
            BriefSection(
                heading="The main question",
                paragraphs=(
                    "The case asks whether Congress gave the agency the power to act.",
                ),
                claim_ids=ids,
            ),
        ),
        argument_analyses=(
            BriefArgumentAnalysis(
                argument_id=ARGUMENT_ID,
                sequence=1,
                argument_date=datetime(2026, 4, 20, tzinfo=UTC),
                heading="What happened in the argument",
                paragraphs=(
                    "The two sides explained their different readings of the law.",
                    "The justices tested how each reading would work in practice.",
                ),
                claim_ids=ids,
            ),
        ),
        claim_ids=ids,
        correction_note=("Corrected after a revised official transcript." if correction else None),
        created_at=NOW + (timedelta(hours=1) if correction else timedelta()),
        generator_model="brief-test",
    )
    history = (
        PublicBriefRevisionSummary(
            revision_number=1,
            maturity=BriefMaturity.OFFICIAL_TRANSCRIPT,
            created_at=NOW,
        ),
        *(
            (
                PublicBriefRevisionSummary(
                    revision_number=2,
                    maturity=BriefMaturity.CORRECTED,
                    created_at=NOW + timedelta(hours=1),
                    correction_note="Corrected after a revised official transcript.",
                ),
            )
            if correction
            else ()
        ),
    )
    return build_public_case(
        term="2025",
        primary_docket="25-466",
        caption="Sripetch v. SEC",
        argument_date=datetime(2026, 4, 20, tzinfo=UTC),
        case_status=ScotusCaseStatus.CORRECTED if correction else ScotusCaseStatus.ARGUED,
        official_detail_url=(
            "https://www.supremecourt.gov/oral_arguments/audio/2025/25-466"
        ),
        revision=revision,
        claims=claims,
        argument_sessions=(
            CaseArgumentSession(
                argument_id=ARGUMENT_ID,
                argument_date=datetime(2026, 4, 20, tzinfo=UTC),
                sequence=1,
                reargument=False,
                official_detail_url=(
                    "https://www.supremecourt.gov/oral_arguments/audio/2025/25-466"
                ),
                official_transcript_url=(
                    "https://www.supremecourt.gov/oral_arguments/transcript.pdf"
                ),
            ),
        ),
        case_history=(
            PublicCaseHistoryEvent(
                status=ScotusCaseStatus.ARGUED,
                changed_at=NOW,
                explanation="The Court heard oral argument.",
            ),
        ),
        revision_history=history,
        topics=("Administrative law", "Statutory interpretation"),
    )


def setup(*, correction: bool = False):  # type: ignore[no-untyped-def]
    store = InMemoryScotusProjectionStore()
    case = public_case(correction=correction)
    projection = store.activate(NOW, NOW, (case,))
    return TestClient(create_scotus_public_app(store)), store, projection, case


def test_public_projection_contains_only_sanitized_case_contract() -> None:
    client, _, _, _ = setup()
    response = client.get("/api/scotus/projection")
    assert response.status_code == 200
    serialized = response.text.lower()
    for forbidden in (
        "object_key",
        "text_private",
        "raw_value",
        "parser",
        "prompt",
        "credential",
        "document_revision_id",
        "observation_id",
    ):
        assert forbidden not in serialized
    assert "not legal advice" in serialized
    assert "not a prediction" in serialized


def test_browse_search_filters_and_stable_case_route() -> None:
    client, _, _, case = setup()
    for path in (
        "/scotus",
        "/scotus/terms/2025",
        "/scotus/arguments/2026-04-20",
        "/scotus/search?q=agency",
        "/scotus?status=argued",
        "/scotus?topic=administrative",
        public_case_path(case),
    ):
        response = client.get(path)
        assert response.status_code == 200
        assert "automated legal analysis" in response.text.lower()
    assert public_case_path(case).endswith(
        "/2025/25-466/2025-25-466-sripetch-v-sec"
    )
    empty = client.get("/scotus/search?q=private-transcript-content")
    assert "no public case briefs" in empty.text.lower()


def test_case_index_sorts_by_court_document_date_not_article_update() -> None:
    base = public_case()
    touched = base.model_copy(
        update={
            "primary_docket": "25-1001",
            "slug": "2025-25-1001-touched",
            "title": "Recently touched",
            "updated_at": NOW + timedelta(days=1),
        }
    )
    latest_argument = base.arguments[0].model_copy(
        update={"argument_date": NOW + timedelta(days=2)}
    )
    argued = base.model_copy(
        update={
            "primary_docket": "25-1002",
            "slug": "2025-25-1002-argued",
            "title": "Recently argued",
            "argument_date": latest_argument.argument_date,
            "arguments": (latest_argument,),
        }
    )
    decision_argument = base.arguments[0].model_copy(
        update={"argument_date": NOW + timedelta(days=1)}
    )
    decided = base.model_copy(
        update={
            "primary_docket": "25-1003",
            "slug": "2025-25-1003-decided",
            "title": "Recently decided",
            "case_status": ScotusCaseStatus.DECIDED,
            "argument_date": decision_argument.argument_date,
            "arguments": (decision_argument,),
            "case_history": (
                PublicCaseHistoryEvent(
                    status=ScotusCaseStatus.DECIDED,
                    changed_at=NOW + timedelta(days=3),
                    explanation="An official Court opinion was verified.",
                ),
            ),
        }
    )
    assert latest_court_document_date(decided) == NOW + timedelta(days=1)
    assert [case.title for case in sort_cases((touched, decided, argued))] == [
        "Recently argued",
        "Recently decided",
        "Recently touched",
    ]


def test_case_index_uses_twenty_item_pages_and_preserves_filters() -> None:
    base = public_case()
    cases = tuple(
        base.model_copy(
            update={
                "primary_docket": f"25-{1000 + index}",
                "slug": f"2025-25-{1000 + index}-case-{index}",
                "title": f"Paging case {index}",
                "argument_date": NOW + timedelta(minutes=index),
                "arguments": (
                    base.arguments[0].model_copy(
                        update={"argument_date": NOW + timedelta(minutes=index)}
                    ),
                ),
            }
        )
        for index in range(25)
    )
    store = InMemoryScotusProjectionStore()
    store.activate(NOW, NOW, cases)
    client = TestClient(create_scotus_public_app(store))

    first = client.get("/scotus?status=argued")
    assert first.status_code == 200
    assert "Showing 1\N{EN DASH}20 of 25 cases" in first.text
    assert "Paging case 24" in first.text
    assert "Paging case 4" not in first.text
    assert "status=argued&amp;page=2" in first.text
    assert client.get("/api/scotus/projection").json()["cases"][0]["title"] == (
        "Paging case 24"
    )

    second = client.get("/scotus?status=argued&page=2")
    assert second.status_code == 200
    assert "Showing 21\N{EN DASH}25 of 25 cases" in second.text
    assert "Paging case 4" in second.text
    assert 'rel="prev"' in second.text
    assert client.get("/scotus?page=3").status_code == 404


def test_case_page_has_accessible_structure_sources_canonical_and_disclosures() -> None:
    client, _, _, case = setup(correction=True)
    response = client.get(public_case_path(case))
    lowered = response.text.lower()
    assert response.status_code == 200
    assert '<main id="content">' in lowered
    assert 'nav aria-label="primary navigation"' in lowered
    assert "skip to content" in lowered
    assert 'rel="canonical"' in lowered
    assert "official supreme court official transcript" in lowered
    assert "not an official supreme court record" in lowered
    assert "not legal advice" in lowered
    assert "correction:" in lowered


def test_multiple_arguments_render_once_in_chronological_case_history() -> None:
    case = public_case()
    first = case.arguments[0]
    second = first.model_copy(
        update={
            "sequence": 2,
            "argument_date": first.argument_date + timedelta(days=30),
            "reargument": True,
            "heading": "What changed in the later argument",
            "paragraphs": (
                "The later argument focused on a narrower reading of the law.",
                "The justices tested how the narrower rule would work in practice.",
            ),
            "official_detail_url": "https://www.supremecourt.gov/reargument",
            "official_transcript_url": (
                "https://www.supremecourt.gov/reargument-transcript.pdf"
            ),
        }
    )
    whole_case = case.model_copy(
        update={"arguments": (first, second), "argument_date": second.argument_date}
    )
    store = InMemoryScotusProjectionStore()
    store.activate(NOW, NOW, (whole_case,))
    client = TestClient(create_scotus_public_app(store))
    page = client.get(public_case_path(whole_case))
    assert page.status_code == 200
    assert "The arguments, in order" in page.text
    assert "Reargument" in page.text
    timeline = page.text.split("The arguments, in order", 1)[1]
    assert timeline.index(str(first.argument_date.date())) < timeline.index(
        str(second.argument_date.date())
    )
    assert client.get(
        f"/scotus/arguments/{first.argument_date.date()}"
    ).status_code == 200
    assert client.get(
        f"/scotus/arguments/{second.argument_date.date()}"
    ).status_code == 200
    with pytest.raises(ValidationError, match="duplicate case pages"):
        store.activate(NOW + timedelta(hours=1), NOW, (whole_case, whole_case))


def test_private_identifier_routes_do_not_exist() -> None:
    client, _, _, _ = setup()
    for path in (
        "/scotus/documents/secret",
        "/scotus/transcripts/secret",
        "/scotus/parser/secret",
        "/scotus/cases/2025/25-466/wrong-slug",
    ):
        assert client.get(path).status_code == 404


def test_failed_projection_keeps_last_known_good() -> None:
    _, store, projection, case = setup()
    store.fail_activation = True
    with pytest.raises(RuntimeError, match="activation"):
        store.activate(NOW + timedelta(hours=1), NOW + timedelta(hours=1), (case,))
    assert store.active_projection() is projection


def test_public_source_links_require_official_court_host() -> None:
    with pytest.raises(ValidationError, match="official Court host"):
        PublicSourceLink(
            evidence_type="Transcript",
            label="Unofficial transcript",
            official_url="https://platform.example/transcript",
            page_label="page 1",
            claim_ids=(uuid4(),),
        )


def test_public_slug_is_deterministic() -> None:
    assert public_case_slug("2025", "25-466", "Sripetch v. SEC") == (
        "2025-25-466-sripetch-v-sec"
    )
