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


def test_numeric_sort_and_consolidated_deduplication() -> None:
    dockets = ["24-304", "24A2", "24-38", "24-7", "24-007"]
    assert sorted(dockets, key=docket_sort_key) == ["24-7", "24-007", "24-38", "24-304", "24A2"]
    assert normalize_dockets(dockets) == ("24-7", "24-38", "24-304", "24A2")


def test_stable_identity_does_not_use_title() -> None:
    assert stable_case_id("No. 24-7") == "scotus-24-7"
    assert stable_case_id(None, unresolved_group="opaque-a").startswith("unresolved-")
    with pytest.raises(ValueError):
        normalize_docket("October term case")
