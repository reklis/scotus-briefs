import pytest
from scotus_guide.dockets import (
    DocketKind,
    docket_sort_key,
    normalize_docket,
    normalize_dockets,
    parse_docket,
    stable_case_id,
)


def test_normalizes_standard_and_application_dockets() -> None:
    assert normalize_docket("No. 24\N{EN DASH}007") == "24-7"
    assert normalize_docket("docket 24a0884") == "24A884"
    assert parse_docket("24A884").kind is DocketKind.APPLICATION


@pytest.mark.parametrize(
    "separator",
    [
        "\N{SOFT HYPHEN}",
        "\N{HYPHEN}",
        "\N{NON-BREAKING HYPHEN}",
        "\N{FIGURE DASH}",
        "\N{EM DASH}",
        "\N{MINUS SIGN}",
    ],
)
def test_normalizes_unicode_docket_separators_and_nbsp(separator: str) -> None:
    label = f"No.\N{NO-BREAK SPACE}24\N{NO-BREAK SPACE}{separator}\N{NO-BREAK SPACE}007"
    assert normalize_docket(label) == "24-7"


def test_normalizes_original_jurisdiction_labels() -> None:
    assert normalize_docket("No. 65, Orig.") == "65O"
    assert normalize_docket("65 Original") == "65O"
    parsed = parse_docket("65O")
    assert parsed.kind is DocketKind.ORIGINAL
    assert parsed.term is None
    assert parsed.number == 65
    with pytest.raises(ValueError):
        normalize_docket("65Orig")


def test_numeric_sort_and_consolidated_deduplication() -> None:
    dockets = ["24-304", "24A2", "24-38", "24-7", "24-007"]
    assert sorted(dockets, key=docket_sort_key) == ["24-7", "24-007", "24-38", "24-304", "24A2"]
    assert normalize_dockets(dockets) == ("24-7", "24-38", "24-304", "24A2")
    assert normalize_dockets(["18-389", "13-389"]) == ("13-389", "18-389")


def test_stable_identity_does_not_use_title() -> None:
    assert stable_case_id("No. 24-7") == "scotus-24-7"
    assert stable_case_id(None, unresolved_group="opaque-a").startswith("unresolved-")
    with pytest.raises(ValueError):
        normalize_docket("October term case")
