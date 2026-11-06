"""Offline regression tests for SEC acceptance timestamp validation.

These are fabricated submissions metadata, not SEC-verified acceptance times.
"""

from __future__ import annotations

import pytest

from tradingagents.dataflows.vendors.sec_filing_discovery import select_filing_candidates


def _submissions(accepted_at: object) -> dict:
    return {
        "cik": "1000184",
        "filings": {
            "recent": {
                "form": ["20-F"],
                "accessionNumber": ["0001104659-25-017815"],
                "filingDate": ["2025-02-27"],
                "primaryDocument": ["sap-2024.htm"],
                "acceptanceDateTime": [accepted_at],
            }
        },
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    "accepted_at",
    [
        "2025-02-27NOT_A_TIMESTAMP",
        "2025-02-27T25:00:00",
        "2025-02-27T09:70:00",
        "2025-02-30T09:30:00",
        "2025-02-27",
        "",
        20250227,
    ],
)
def test_invalid_acceptance_timestamp_fails_closed(accepted_at):
    with pytest.raises(ValueError, match="acceptance timestamp"):
        select_filing_candidates(
            _submissions(accepted_at), cik="1000184", as_of_date="2025-02-27"
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "accepted_at",
    [
        "2025-02-27T09:30:00",
        "2025-02-27T09:30:00Z",
        "2025-02-27T09:30:00.000Z",
        "2025-02-27T09:30:00-05:00",
    ],
)
def test_well_formed_acceptance_timestamp_retained(accepted_at):
    selected = select_filing_candidates(
        _submissions(accepted_at), cik="1000184", as_of_date="2025-02-27"
    )
    assert len(selected) == 1
    assert selected[0].accepted_at == accepted_at


@pytest.mark.unit
def test_future_acceptance_date_excluded_even_if_filing_date_eligible():
    selected = select_filing_candidates(
        _submissions("2025-02-28T01:00:00.000Z"),
        cik="1000184",
        as_of_date="2025-02-27",
    )
    assert selected == []
