from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from ragchew.scotus.contracts import BriefMaturity, ScotusCaseStatus
from ragchew.scotus.public import create_scotus_public_app, public_case_path
from ragchew.scotus.public_contracts import (
    PublicBriefRevisionSummary,
    PublicCaseBrief,
    PublicDisposition,
    ScotusPublicProjection,
    derive_latest_court_document_date,
    public_case_slug,
)
from ragchew.scotus.publishing import InMemoryScotusProjectionStore
from ragchew.scotus.static_contracts import (
    ReleaseFile,
    ReleaseManifest,
    StaticSearchIndex,
    canonical_json_bytes,
    sha256_hex,
)
from ragchew.scotus.static_export import (
    MANIFEST_PATH,
    StaticSiteExporter,
    content_release_id,
)
from ragchew.scotus.static_urls import StaticUrlPolicy, sort_cases
from ragchew.scotus.static_validation import StaticValidationError, validate_static_candidate

EPOCH = datetime(2026, 8, 28, 3, 17, tzinfo=UTC)
SOURCE_COMMIT = "a" * 40
CONFIG_DIGEST = "b" * 64


def _base_case() -> PublicCaseBrief:
    payload = json.loads(
        Path("tests/fixtures/static/one-case.json").read_text(encoding="utf-8")
    )
    return PublicCaseBrief.model_validate(payload["projection"]["cases"][0])


def _case(
    docket: str,
    title: str,
    *,
    argument_date: datetime | None,
    reargument_date: datetime | None = None,
    publication_date: datetime | None = None,
    revision_date: datetime | None = None,
) -> PublicCaseBrief:
    base = _base_case()
    arguments = ()
    if argument_date is not None:
        first_argument = base.arguments[0].model_copy(
            update={"argument_date": argument_date}
        )
        arguments = (first_argument,)
        if reargument_date is not None:
            arguments = (
                first_argument,
                first_argument.model_copy(
                    update={
                        "sequence": 2,
                        "argument_date": reargument_date,
                        "reargument": True,
                        "heading": "Synthetic reargument",
                    }
                ),
            )
    dispositions = ()
    if publication_date is not None:
        dispositions = (
            PublicDisposition(
                kind="opinion",
                official_url=(
                    "https://www.supremecourt.gov/opinions/25pdf/"
                    f"{docket.casefold()}_synthetic.pdf"
                ),
                publication_date=publication_date,
                revision_date=revision_date,
            ),
        )
    latest = derive_latest_court_document_date(arguments, dispositions)
    disposition_source = None
    if not arguments:
        disposition_source = base.title_sources[0].model_copy(
            update={
                "evidence_type": "Official disposition",
                "label": "Synthetic official disposition",
                "official_url": dispositions[0].official_url,
                "page_label": "file page 1",
            }
        )
    revisions = (
        base.revisions[0],
        PublicBriefRevisionSummary(
            revision_number=2,
            maturity=BriefMaturity.CORRECTED,
            created_at=EPOCH,
            correction_note="Synthetic correction for ordering coverage.",
        ),
    )
    payload = base.model_dump(mode="python")
    payload.update(
        {
            "primary_docket": docket,
            "caption": f"Synthetic {docket} v. Agency",
            "slug": public_case_slug("2025", docket, f"Synthetic {docket} v. Agency"),
            "title": title,
            "argument_date": (
                max(argument.argument_date for argument in arguments)
                if arguments
                else None
            ),
            "latest_court_document_date": latest,
            "arguments": arguments,
            "official_detail_url": (
                "https://www.supremecourt.gov/oral_arguments/audio/2025/synthetic"
                if arguments
                else None
            ),
            "dispositions": dispositions,
            "case_status": ScotusCaseStatus.CORRECTED,
            "maturity": BriefMaturity.CORRECTED,
            "revisions": revisions,
            "topics": ("Shared activity topic",),
            **(
                {
                    "title_sources": (disposition_source,),
                    "dek_sources": (disposition_source,),
                    "sections": tuple(
                        section.model_copy(update={"sources": (disposition_source,)})
                        for section in base.sections
                    ),
                }
                if disposition_source is not None
                else {}
            ),
        }
    )
    return PublicCaseBrief.model_validate(payload)


def _mixed_projection() -> ScotusPublicProjection:
    january = datetime(2026, 1, 10, tzinfo=UTC)
    cases = (
        _case(
            "25A105",
            "Disposition only newest",
            argument_date=None,
            publication_date=datetime(2026, 6, 7, tzinfo=UTC),
        ),
        _case(
            "25-104",
            "Revised opinion",
            argument_date=january,
            publication_date=datetime(2026, 6, 1, tzinfo=UTC),
            revision_date=datetime(2026, 6, 6, tzinfo=UTC),
        ),
        _case(
            "25-102",
            "Later opinion",
            argument_date=january,
            reargument_date=datetime(2026, 2, 10, tzinfo=UTC),
            publication_date=datetime(2026, 6, 5, tzinfo=UTC),
        ),
        _case(
            "25-103",
            "Tied disposition",
            argument_date=january,
            publication_date=datetime(2026, 6, 4, tzinfo=UTC),
        ),
        _case(
            "25-106",
            "Tied argument",
            argument_date=datetime(2026, 6, 4, tzinfo=UTC),
        ),
    )
    # Deliberately store them in neither activity nor tie-break order.
    return ScotusPublicProjection(
        watermark=EPOCH,
        generated_at=EPOCH,
        cases=(cases[3], cases[0], cases[4], cases[2], cases[1]),
    )


def _export_mixed(tmp_path: Path, *, page_size: int = 2) -> tuple[Path, StaticUrlPolicy]:
    urls = StaticUrlPolicy("https://example.test", "/", "/scotus/")
    output = tmp_path / "site"
    StaticSiteExporter(urls, page_size=page_size).export(
        _mixed_projection(),
        output,
        source_commit=SOURCE_COMMIT,
        build_epoch=EPOCH,
        config_sha256=CONFIG_DIGEST,
    )
    return output, urls


def _listing_links(root: Path, relative: str) -> tuple[str, ...]:
    first = root / relative / "index.html"
    pages = [first]
    page_root = first.parent / "page"
    if page_root.exists():
        pages.extend(
            path
            for _, path in sorted(
                (int(path.parent.name), path)
                for path in page_root.glob("*/index.html")
            )
        )
    return tuple(
        link
        for page in pages
        for link in re.findall(
            r'<article class="case-card"[^>]*>.*?<h2><a href="([^"]+)"',
            page.read_text(encoding="utf-8"),
            re.DOTALL,
        )
    )


def _refresh_manifest(root: Path) -> None:
    path = root / MANIFEST_PATH
    prior = ReleaseManifest.model_validate_json(path.read_bytes())
    files = tuple(
        ReleaseFile(
            path=item.relative_to(root).as_posix(),
            sha256=sha256_hex(item.read_bytes()),
            byte_count=item.stat().st_size,
        )
        for item in sorted(candidate for candidate in root.rglob("*") if candidate.is_file())
        if item.relative_to(root) != Path(MANIFEST_PATH)
    )
    release_id = content_release_id(
        files=files,
        source_commit=prior.source_commit,
        previous_release_id=prior.previous_release_id,
        projection_sha256=prior.projection_sha256,
        config_sha256=prior.config_sha256,
        tool_version=prior.tool_version,
        case_count=prior.case_count,
        page_count=prior.page_count,
    )
    path.write_bytes(
        canonical_json_bytes(prior.model_copy(update={"files": files, "release_id": release_id}))
    )


def test_mixed_activity_uses_one_exact_order_on_every_static_surface(
    tmp_path: Path,
) -> None:
    output, urls = _export_mixed(tmp_path)
    projection = _mixed_projection()
    expected_cases = sort_cases(projection.cases)
    expected = tuple(urls.case(case) for case in expected_cases)
    assert [case.title for case in expected_cases] == [
        "Disposition only newest",
        "Revised opinion",
        "Later opinion",
        "Tied disposition",
        "Tied argument",
    ]

    search = StaticSearchIndex.model_validate_json(
        (output / "data/v1/search.json").read_bytes()
    )
    assert tuple(item.path for item in search.cases) == expected
    assert search.cases[0].argument_date is None
    assert search.cases[0].latest_court_document_date == "2026-06-07"

    for relative in (
        Path("scotus"),
        Path("scotus/terms/2025"),
        Path("scotus/statuses/corrected"),
        Path("scotus/topics/shared-activity-topic"),
        Path("scotus/corrections"),
    ):
        assert _listing_links(output, relative.as_posix()) == expected
        assert "newest official Court activity first" in (
            output / relative / "index.html"
        ).read_text(encoding="utf-8")

    january_expected = tuple(
        urls.case(case)
        for case in expected_cases
        if any(
            argument.argument_date.date().isoformat() == "2026-01-10"
            for argument in case.arguments
        )
    )
    assert _listing_links(output, "scotus/arguments/2026-01-10") == january_expected
    assert expected[0] not in january_expected
    later_opinion = next(case for case in expected_cases if case.title == "Later opinion")
    assert _listing_links(output, "scotus/arguments/2026-02-10") == (
        urls.case(later_opinion),
    )

    root_html = (output / "index.html").read_text(encoding="utf-8")
    search_html = (output / "scotus/search/index.html").read_text(encoding="utf-8")
    assert 'data-index="/data/v1/search.json"' in root_html
    assert 'data-index="/data/v1/search.json"' in search_html
    javascript = next((output / "assets").glob("scotus-search.*.js")).read_text(
        encoding="utf-8"
    )
    assert "Latest official Court activity" in javascript
    assert ".sort(" not in javascript
    validate_static_candidate(output, urls)


def test_export_normalizes_offsets_and_duplicate_topics_before_every_surface(
    tmp_path: Path,
) -> None:
    eastern = timezone(timedelta(hours=-4))
    case = _case(
        "25-300",
        "Offset activity",
        argument_date=datetime(2026, 6, 1, 23, 30, tzinfo=eastern),
    )
    case = case.model_copy(update={"topics": ("Shared activity topic",) * 2})
    projection = ScotusPublicProjection(
        watermark=EPOCH,
        generated_at=EPOCH,
        cases=(case,),
    )
    urls = StaticUrlPolicy("https://example.test", "/", "/scotus/")
    output = tmp_path / "normalized"
    StaticSiteExporter(urls).export(
        projection,
        output,
        source_commit=SOURCE_COMMIT,
        build_epoch=EPOCH,
        config_sha256=CONFIG_DIGEST,
    )
    search = StaticSearchIndex.model_validate_json(
        (output / "data/v1/search.json").read_bytes()
    )
    assert search.cases[0].latest_court_document_date == "2026-06-02"
    assert search.cases[0].topics == ("Shared activity topic",)
    listing = (output / "scotus/index.html").read_text(encoding="utf-8")
    assert listing.count(">Shared activity topic</a>") == 1
    assert ">2026-06-02</time>" in listing
    validate_static_candidate(output, urls)


def test_dynamic_filters_archives_search_and_pagination_keep_shared_order() -> None:
    projection = _mixed_projection()
    store = InMemoryScotusProjectionStore()
    store.activate(projection.watermark, projection.generated_at, projection.cases)
    client = TestClient(create_scotus_public_app(store))
    expected_titles = [case.title for case in sort_cases(projection.cases)]

    for path in (
        "/scotus",
        "/scotus?status=corrected",
        "/scotus?topic=shared",
        "/scotus/terms/2025",
        "/scotus/statuses/corrected",
        "/scotus/topics/shared-activity-topic",
        "/scotus/corrections",
        "/scotus/search",
        "/scotus/search?q=2025",
        "/scotus/search?status=corrected&topic=Shared%20activity%20topic",
    ):
        response = client.get(path)
        assert response.status_code == 200
        positions = [response.text.index(title) for title in expected_titles]
        assert positions == sorted(positions), path

    zero_session = projection.cases[1]
    page = client.get(public_case_path(zero_session))
    assert page.status_code == 200
    assert "Latest official Court activity" in page.text
    assert "argument-history" not in page.text
    assert "argument-date" not in page.text
    january = client.get("/scotus/arguments/2026-01-10")
    assert zero_session.title not in january.text


def test_dynamic_every_listing_preserves_order_across_page_boundaries() -> None:
    cases = tuple(
        _case(
            f"25-{200 + index}",
            f"Pagination case {index:02d}",
            argument_date=EPOCH + timedelta(minutes=index),
        )
        for index in range(25)
    )
    projection = ScotusPublicProjection(
        watermark=EPOCH,
        generated_at=EPOCH,
        cases=tuple(reversed(cases)),
    )
    store = InMemoryScotusProjectionStore()
    store.activate(projection.watermark, projection.generated_at, projection.cases)
    client = TestClient(create_scotus_public_app(store))
    expected = tuple(case.title for case in sort_cases(projection.cases))

    for route in (
        "/scotus",
        "/scotus?status=corrected",
        "/scotus?topic=shared",
        "/scotus/terms/2025",
        "/scotus/statuses/corrected",
        "/scotus/topics/shared-activity-topic",
        "/scotus/corrections",
        "/scotus/search?q=pagination",
    ):
        separator = "&" if "?" in route else "?"
        first = client.get(route)
        second = client.get(f"{route}{separator}page=2")
        assert first.status_code == second.status_code == 200
        actual = tuple(
            re.findall(r"<h2><a [^>]*>([^<]+)</a></h2>", first.text + second.text)
        )
        assert actual == expected, route


def test_zero_session_contract_rejects_argument_detail_metadata() -> None:
    zero_session = next(case for case in _mixed_projection().cases if not case.arguments)
    payload = zero_session.model_dump(mode="python")
    payload["official_detail_url"] = (
        "https://www.supremecourt.gov/oral_arguments/audio/2025/invented"
    )
    with pytest.raises(ValidationError, match="zero-argument case"):
        PublicCaseBrief.model_validate(payload)


def test_validation_rejects_reordered_listing_and_zero_session_markup(
    tmp_path: Path,
) -> None:
    output, urls = _export_mixed(tmp_path, page_size=10)
    listing = output / "scotus/index.html"
    html = listing.read_text(encoding="utf-8")
    cards = re.findall(r'<li><article class="case-card".*?</article></li>', html, re.DOTALL)
    assert len(cards) == 5
    placeholder = "<!-- reordered-card-placeholder -->"
    html = html.replace(cards[0], placeholder, 1)
    html = html.replace(cards[1], cards[0], 1)
    html = html.replace(placeholder, cards[1], 1)
    listing.write_text(html, encoding="utf-8")
    _refresh_manifest(output)
    with pytest.raises(StaticValidationError, match="exact newest-first order"):
        validate_static_candidate(output, urls)

    search_site, search_urls = _export_mixed(tmp_path / "search", page_size=10)
    search_path = search_site / "data/v1/search.json"
    search_payload = json.loads(search_path.read_bytes())
    search_payload["cases"] = list(reversed(search_payload["cases"]))
    search_path.write_bytes(canonical_json_bytes(search_payload))
    _refresh_manifest(search_site)
    with pytest.raises(StaticValidationError, match="exact newest-first order"):
        validate_static_candidate(search_site, search_urls)

    visible_site, visible_urls = _export_mixed(tmp_path / "visible", page_size=10)
    visible_listing = visible_site / "scotus/index.html"
    visible_html = visible_listing.read_text(encoding="utf-8").replace(
        ">2026-06-07</time>", ">2026-06-01</time>", 1
    )
    visible_listing.write_text(visible_html, encoding="utf-8")
    _refresh_manifest(visible_site)
    with pytest.raises(StaticValidationError, match="activity markup"):
        validate_static_candidate(visible_site, visible_urls)

    label_site, label_urls = _export_mixed(tmp_path / "label", page_size=10)
    label_listing = label_site / "scotus/index.html"
    label_listing.write_text(
        label_listing.read_text(encoding="utf-8").replace(
            "Latest official Court activity", "Latest article update", 1
        ),
        encoding="utf-8",
    )
    _refresh_manifest(label_site)
    with pytest.raises(StaticValidationError, match="activity markup"):
        validate_static_candidate(label_site, label_urls)

    second, second_urls = _export_mixed(tmp_path / "second", page_size=10)
    zero = next(case for case in _mixed_projection().cases if not case.arguments)
    case_page = second / second_urls.output_relative(second_urls.case(zero))
    case_html = case_page.read_text(encoding="utf-8").replace(
        '<dl class="meta">',
        '<dl class="meta"><div class="argument-date">Invented argument</div>',
        1,
    )
    case_page.write_text(case_html, encoding="utf-8")
    _refresh_manifest(second)
    with pytest.raises(StaticValidationError, match="argument markup"):
        validate_static_candidate(second, second_urls)

    link_site, link_urls = _export_mixed(tmp_path / "link", page_size=10)
    link_page = link_site / link_urls.output_relative(link_urls.case(zero))
    link_html = link_page.read_text(encoding="utf-8").replace(
        "</article>",
        '<a href="https://www.supremecourt.gov/oral_arguments/invented">'
        "Official transcript</a></article>",
        1,
    )
    link_page.write_text(link_html, encoding="utf-8")
    _refresh_manifest(link_site)
    with pytest.raises(StaticValidationError, match="argument markup"):
        validate_static_candidate(link_site, link_urls)
