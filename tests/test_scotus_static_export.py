from __future__ import annotations

import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ragchew.scotus.public_contracts import ScotusPublicProjection, public_case_key
from ragchew.scotus.static_cli import build_parser
from ragchew.scotus.static_contracts import ReleaseManifest, StaticSearchIndex
from ragchew.scotus.static_export import StaticExportError, StaticSiteExporter
from ragchew.scotus.static_pipeline import ProductionBatchUnavailable
from ragchew.scotus.static_urls import StaticUrlPolicy
from ragchew.scotus.static_validation import StaticValidationError, validate_static_candidate

EPOCH = datetime(2026, 8, 28, 3, 17, tzinfo=UTC)
SOURCE_COMMIT = "a" * 40
CONFIG_DIGEST = "b" * 64


def fixture(name: str) -> ScotusPublicProjection:
    payload = json.loads(Path(f"tests/fixtures/static/{name}.json").read_text(encoding="utf-8"))
    return ScotusPublicProjection.model_validate(payload["projection"])


def export(
    tmp_path: Path,
    name: str = "multiple-terms",
    *,
    base: str = "/ragchew/",
    page_size: int = 20,
    epoch: datetime = EPOCH,
    legacy_slugs: dict[str, tuple[str, ...]] | None = None,
) -> tuple[Path, StaticUrlPolicy, str]:
    urls = StaticUrlPolicy("https://example.test", base, "/scotus/")
    output = tmp_path / f"site-{len(tuple(tmp_path.iterdir()))}"
    result = StaticSiteExporter(urls, page_size=page_size).export(
        fixture(name),
        output,
        source_commit=SOURCE_COMMIT,
        build_epoch=epoch,
        config_sha256=CONFIG_DIGEST,
        legacy_slugs=legacy_slugs,
    )
    return output, urls, result.manifest.release_id


def tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_workflow_cli_contract_and_fixture_preview_builds_then_exits(tmp_path: Path) -> None:
    parser = build_parser()
    help_text = parser.format_help()
    # Subcommand choices are included in argparse's top-level usage.
    for command in (
        "fixture-preview",
        "validate",
        "reconcile",
        "batch",
        "persist-cost-receipts",
        "promote",
    ):
        assert command in help_text

    output = tmp_path / "fixture-site"
    github_output = tmp_path / "github-output"
    args = parser.parse_args(
        [
            "fixture-preview",
            "--output",
            str(output),
            "--source-commit",
            SOURCE_COMMIT,
            "--build-epoch",
            EPOCH.isoformat(),
            "--github-output",
            str(github_output),
        ]
    )
    assert args.function(args) == 0
    manifest = ReleaseManifest.model_validate_json(
        (output / "release/v1/release.json").read_bytes()
    )
    outputs = dict(
        line.split("=", 1) for line in github_output.read_text(encoding="utf-8").splitlines()
    )
    assert outputs["release_id"] == manifest.release_id
    assert outputs["release_changed"] == "true"
    assert outputs["publication_ready"] == "false"

    validate_args = parser.parse_args(
        ["validate", "--output", str(output), "--privacy-scan"]
    )
    assert validate_args.function(validate_args) == 0


def test_receipt_upload_validation_rejects_private_payload_with_sanitized_error(
    tmp_path: Path,
) -> None:
    receipt = tmp_path / "receipts.json"
    forbidden_token = "sk-" + ("a" * 32)
    receipt.write_text(
        json.dumps({"receipts": [{"prompt": forbidden_token}]}),
        encoding="utf-8",
    )
    args = build_parser().parse_args(
        ["persist-cost-receipts", "--receipts", str(receipt), "--validate-only"]
    )
    with pytest.raises(ValueError) as raised:
        args.function(args)
    assert str(raised.value) == "cost receipt bundle failed privacy/contract validation"
    assert "sk-" not in str(raised.value)


def test_unconfigured_live_batch_stops_before_workspace_or_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("RAGCHEW_SCOTUS_BATCH_ADAPTER", raising=False)
    args = build_parser().parse_args(
        [
            "batch",
            "--mode",
            "nightly",
            "--state-dir",
            str(tmp_path / "state"),
            "--candidate-state-dir",
            str(tmp_path / "candidate"),
            "--output",
            str(tmp_path / "site"),
            "--workspace",
            str(tmp_path / "private"),
        ]
    )
    with pytest.raises(ProductionBatchUnavailable, match="before network/model use"):
        args.function(args)
    assert not any(tmp_path.iterdir())


def test_export_is_byte_deterministic_and_content_derived(tmp_path: Path) -> None:
    first, _, first_id = export(tmp_path)
    second, _, second_id = export(tmp_path)
    assert tree_bytes(first) == tree_bytes(second)
    assert first_id == second_id

    later, _, later_id = export(tmp_path, epoch=datetime(2026, 8, 29, tzinfo=UTC))
    assert later_id == first_id
    assert (later / "scotus/index.html").read_bytes() == (first / "scotus/index.html").read_bytes()


def test_project_and_custom_domain_urls_are_confined_and_official_links_unchanged(
    tmp_path: Path,
) -> None:
    project, _, _ = export(tmp_path, name="one-case")
    html = (project / "scotus/index.html").read_text(encoding="utf-8")
    assert 'href="/ragchew/scotus/' in html
    assert 'href="/scotus' not in html
    assert "/api/scotus" not in html
    case_html = next((project / "scotus/cases").rglob("index.html")).read_text(encoding="utf-8")
    assert "https://www.supremecourt.gov/" in case_html

    root, _, _ = export(tmp_path, name="one-case", base="/")
    root_html = (root / "scotus/index.html").read_text(encoding="utf-8")
    assert 'href="/scotus/' in root_html
    assert 'href="//scotus/' not in root_html
    assert 'href="https://example.test/scotus/' in next(
        (root / "scotus/cases").rglob("index.html")
    ).read_text(encoding="utf-8")


def test_complete_tree_has_archives_pagination_json_and_empty_state(tmp_path: Path) -> None:
    output, urls, _ = export(tmp_path, page_size=1)
    for path in (
        "index.html",
        "404.html",
        ".nojekyll",
        "robots.txt",
        "sitemap.xml",
        "scotus/index.html",
        "scotus/page/2/index.html",
        "scotus/terms/2024/index.html",
        "scotus/terms/2025/index.html",
        "scotus/arguments/2026-08-28/index.html",
        "scotus/statuses/argued/index.html",
        "scotus/topics/synthetic-law/index.html",
        "scotus/search/index.html",
        "scotus/corrections/index.html",
        "data/v1/projection.json",
        "data/v1/search.json",
        "release/v1/release.json",
    ):
        assert (output / path).is_file(), path
    validate_static_candidate(output, urls)

    empty, _, _ = export(tmp_path, name="empty-bootstrap")
    assert not (empty / "scotus/cases").exists()
    assert "No public case briefs" in (empty / "scotus/index.html").read_text(encoding="utf-8")


def test_search_index_is_strict_minimal_and_deterministically_ordered(tmp_path: Path) -> None:
    output, _, _ = export(tmp_path)
    index = StaticSearchIndex.model_validate_json((output / "data/v1/search.json").read_bytes())
    assert [entry.term for entry in index.cases] == ["2025", "2024"]
    assert set(index.cases[0].model_dump()) == {
        "path",
        "title",
        "caption",
        "docket",
        "term",
        "argument_date",
        "status",
        "topics",
    }
    serialized = (output / "data/v1/search.json").read_text(encoding="utf-8")
    assert "sections" not in serialized
    assert "paragraphs" not in serialized


def test_legacy_slug_redirect_and_correction_history(tmp_path: Path) -> None:
    projection = fixture("correction")
    case = projection.cases[0]
    key = public_case_key(case.term, case.primary_docket)
    old_slug = "2025-25-466-synthetic-example-v-agency"
    output, urls, _ = export(
        tmp_path,
        name="correction",
        legacy_slugs={key: (old_slug,)},
    )
    redirect = output / urls.output_relative(urls.case(case, slug=old_slug))
    assert 'http-equiv="refresh"' in redirect.read_text(encoding="utf-8")
    assert "Corrected from revised synthetic source" in (
        output / "scotus/corrections/index.html"
    ).read_text(encoding="utf-8")


def test_validation_rejects_mutation_extra_json_and_private_files(tmp_path: Path) -> None:
    output, urls, _ = export(tmp_path)
    index = output / "scotus/index.html"
    index.write_text(index.read_text(encoding="utf-8") + "changed", encoding="utf-8")
    with pytest.raises(StaticValidationError, match="manifest"):
        validate_static_candidate(output, urls)

    second, second_urls, _ = export(tmp_path)
    (second / "extra.json").write_text('{"safe":"but not allowlisted"}\n', encoding="utf-8")
    with pytest.raises(StaticValidationError, match="outside the allowlisted"):
        validate_static_candidate(second, second_urls)

    third, third_urls, _ = export(tmp_path)
    (third / "document.pdf").write_bytes(b"%PDF-1.7 synthetic")
    with pytest.raises(StaticValidationError, match=r"prohibited|forbidden"):
        validate_static_candidate(third, third_urls)


def test_export_failure_leaves_prior_site_and_candidate_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prior, _, _ = export(tmp_path)
    prior_bytes = tree_bytes(prior)
    candidate = tmp_path / "failed-candidate"
    exporter = StaticSiteExporter(StaticUrlPolicy("https://example.test", "/ragchew/", "/scotus/"))

    def fail(*args: object, **kwargs: object) -> object:
        raise RuntimeError("synthetic rendering failure")

    monkeypatch.setattr(exporter, "_render_tree", fail)
    with pytest.raises(RuntimeError, match="rendering failure"):
        exporter.export(
            fixture("one-case"),
            candidate,
            source_commit=SOURCE_COMMIT,
            build_epoch=EPOCH,
            config_sha256=CONFIG_DIGEST,
        )
    assert not candidate.exists()
    assert tree_bytes(prior) == prior_bytes


def test_colliding_topic_routes_fail_closed(tmp_path: Path) -> None:
    projection = fixture("one-case")
    case = projection.cases[0].model_copy(update={"topics": ("Tax", "Tax!")})
    collision = projection.model_copy(update={"cases": (case,)})
    exporter = StaticSiteExporter(StaticUrlPolicy("https://example.test", "/ragchew/", "/scotus/"))
    with pytest.raises(StaticExportError, match="same archive path"):
        exporter.export(
            collision,
            tmp_path / "collision",
            source_commit=SOURCE_COMMIT,
            build_epoch=EPOCH,
            config_sha256=CONFIG_DIGEST,
        )


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not available")
def test_search_javascript_matches_filters_pages_and_treats_markup_as_text() -> None:
    script = """
const search = require('./static/scotus-search.js');
const rows = [
 {title:'<img onerror=alert(1)> Tax',caption:'Alpha',docket:'2',term:'2025',
  argument_date:'2026-01-02',status:'argued',topics:['Tax'],path:'/b/'},
 {title:'Other',caption:'Beta',docket:'1',term:'2024',argument_date:'2025-01-02',
  status:'decided',topics:['Civil Rights'],path:'/a/'}
];
if (search.normalize('  ALPHA   Beta ') !== 'alpha beta') process.exit(1);
if (search.filterCases(rows, '<IMG', '', '').length !== 1) process.exit(2);
if (search.filterCases(rows, '', 'DECIDED', 'civil rights')[0].path !== '/a/') process.exit(3);
if (search.pageCases(Array(21).fill(rows[0]), 2).items.length !== 1) process.exit(4);
const source = require('fs').readFileSync('./static/scotus-search.js','utf8');
if (source.includes('innerHTML')) process.exit(5);
"""
    subprocess.run(["node", "-e", script], check=True)
