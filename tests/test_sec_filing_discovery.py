"""Offline, synthetic SEC submissions fixtures for candidate selection.

Dates and the FNV accession are historical reference labels; this fabricated
submissions payload is not an independently verified SEC response.
"""

from __future__ import annotations

import copy

import pytest

from tradingagents.dataflows.vendors.sec_filing_discovery import select_filing_candidates


@pytest.fixture
def submissions():
    return {
        "cik": 1456346,
        "filings": {"recent": {
            "form": ["6-K", "40-F", "6-K", "8-K"],
            "accessionNumber": [
                "0001104659-26-094181", "0001104659-26-033333",
                "0001104659-26-099999", "0001104659-26-088888",
            ],
            "filingDate": ["2026-08-12", "2026-03-19", "2026-10-31", "2026-07-14"],
            "acceptanceDateTime": [
                "2026-08-12T17:23:00", "2026-03-19T09:45:00",
                "2026-10-31T08:30:00", "2026-07-14T15:00:00",
            ],
            "reportDate": ["2026-06-30", "2025-12-31", "2026-09-30", ""],
            "primaryDocument": ["fnv-6k.htm", "fnv-40f.htm", "future.htm", "other.htm"],
            # SEC summary flags are hints only; the archive must be inspected later.
            "isXBRL": [0, 1, 1, 1],
        }},
    }


@pytest.mark.unit
def test_as_of_date_is_inclusive_and_filters_future_and_irrelevant_forms(submissions):
    earlier = select_filing_candidates(submissions, cik="0001456346", as_of_date="2026-08-11")
    assert [item.form for item in earlier] == ["40-F"]
    on_date = select_filing_candidates(submissions, cik="1456346", as_of_date="2026-08-12")
    assert [item.form for item in on_date] == ["6-K", "40-F"]
    assert on_date[0].filing_date == "2026-08-12"


@pytest.mark.unit
def test_archive_directory_uses_cik_and_accession_not_guessed_xbrl_url(submissions):
    candidate = select_filing_candidates(submissions, cik="1456346", as_of_date="2026-08-12")[0]
    assert candidate.cik == "0001456346"
    assert candidate.accession_number == "0001104659-26-094181"
    assert candidate.archive_directory == (
        "https://www.sec.gov/Archives/edgar/data/1456346/000110465926094181/"
    )
    assert candidate.primary_document == "fnv-6k.htm"
    assert not hasattr(candidate, "xbrl_instance_url")


@pytest.mark.unit
def test_acceptance_date_after_as_of_is_not_used_even_if_filing_date_is_older(submissions):
    adjusted = copy.deepcopy(submissions)
    adjusted["filings"]["recent"]["acceptanceDateTime"][0] = "2026-08-13T01:00:00"
    found = select_filing_candidates(adjusted, cik="1456346", as_of_date="2026-08-12")
    assert [item.form for item in found] == ["40-F"]


@pytest.mark.unit
def test_wrong_cik_rejected_before_source_url_is_constructed(submissions):
    with pytest.raises(ValueError, match="CIK does not match"):
        select_filing_candidates(submissions, cik="0000320193", as_of_date="2026-08-12")


@pytest.mark.unit
@pytest.mark.parametrize("unsafe_name", ["../evil.xml", "file/evil.htm", "", "../.env"])
def test_unsafe_primary_document_is_rejected(submissions, unsafe_name):
    adjusted = copy.deepcopy(submissions)
    adjusted["filings"]["recent"]["primaryDocument"][0] = unsafe_name
    with pytest.raises(ValueError, match="Unsafe SEC primary document"):
        select_filing_candidates(adjusted, cik="1456346", as_of_date="2026-08-12")


@pytest.mark.unit
def test_unaligned_submissions_columns_fail_closed(submissions):
    adjusted = copy.deepcopy(submissions)
    adjusted["filings"]["recent"]["accessionNumber"].pop()
    with pytest.raises(ValueError, match="misaligned column"):
        select_filing_candidates(adjusted, cik="1456346", as_of_date="2026-08-12")


@pytest.mark.unit
def test_repeated_identical_accession_not_reported_twice(submissions):
    adjusted = copy.deepcopy(submissions)
    recent = adjusted["filings"]["recent"]
    for column in recent.values():
        column.append(column[0])
    found = select_filing_candidates(adjusted, cik="1456346", as_of_date="2026-08-12")
    assert len(found) == 2


@pytest.mark.unit
def test_historical_submission_file_extends_filing_discovery(submissions):
    adjusted = copy.deepcopy(submissions)
    filename = 'CIK0001456346-submissions-001.json'
    adjusted['filings']['files'] = [{
        'name': filename,
        'filingFrom': '2024-03-15',
        'filingTo': '2024-03-15',
        'filingCount': 1,
    }]
    historical = {
        'form': ['20-F'],
        'accessionNumber': ['0001104659-24-023456'],
        'filingDate': ['2024-03-15'],
        'acceptanceDateTime': ['2024-03-15T16:10:00'],
        'reportDate': ['2023-12-31'],
        'primaryDocument': ['historical-20f.htm'],
    }
    options = {'cik': '1456346', 'historical_files': {filename: historical}}
    found = select_filing_candidates(adjusted, as_of_date='2024-03-15', **options)
    assert len(found) == 1
    assert found[0].form == '20-F'
    assert found[0].accession_number == '0001104659-24-023456'
    assert found[0].report_date == '2023-12-31'
    earlier = select_filing_candidates(adjusted, as_of_date='2024-03-14', **options)
    assert earlier == []


@pytest.mark.unit
@pytest.mark.parametrize('fault, expected', [
    ('missing', 'incomplete or unlisted'),
    ('unlisted', 'incomplete or unlisted'),
    ('count', 'filingCount mismatch'),
    ('range', 'outside manifest date range'),
    ('conflict', 'Conflicting SEC metadata'),
])
def test_historical_manifest_rejects_inconsistent_records(submissions, fault, expected):
    adjusted = copy.deepcopy(submissions)
    filename = 'CIK0001456346-submissions-001.json'
    descriptor = {'name': filename, 'filingFrom': '2024-03-15', 'filingTo': '2024-03-15', 'filingCount': 1}
    adjusted['filings']['files'] = [descriptor]
    records = {
        'form': ['20-F'],
        'accessionNumber': ['0001104659-24-023456'],
        'filingDate': ['2024-03-15'],
        'acceptanceDateTime': ['2024-03-15T16:10:00'],
        'reportDate': ['2023-12-31'],
        'primaryDocument': ['historical-20f.htm'],
    }
    supplied = {filename: records}
    if fault == 'missing':
        supplied.clear()
    elif fault == 'unlisted':
        supplied['not-in-manifest.json'] = records
    elif fault == 'count':
        descriptor['filingCount'] = 2
    elif fault == 'range':
        records['filingDate'][0] = '2024-03-16'
    else:
        recent = adjusted['filings']['recent']
        records['accessionNumber'][0] = recent['accessionNumber'][0]
        records['filingDate'][0] = recent['filingDate'][0]
        descriptor['filingTo'] = records['filingDate'][0]
    with pytest.raises(ValueError, match=expected):
        select_filing_candidates(adjusted, cik='1456346', as_of_date='2026-08-12', historical_files=supplied)

@pytest.mark.unit
def test_beneficial_ownership_amendments_are_not_issuer_financial_candidates(submissions):
    """SCHEDULE 13G/A rows are not restated 6-Ks despite their '/A' suffix."""
    adjusted = copy.deepcopy(submissions)
    recent = adjusted["filings"]["recent"]
    ownership = (
        ("2026-10-27", "2026-10-27T21:25:30.000Z",
         "0002012383-26-003722"),
        ("2026-05-06", "2026-05-06T15:18:37.000Z",
         "0000315066-26-001194"),
    )
    for filed, accepted, accession in ownership:
        record = {
            "form": "SCHEDULE 13G/A",
            "accessionNumber": accession,
            "filingDate": filed,
            "acceptanceDateTime": accepted,
            "reportDate": "",
            "primaryDocument": "xslSCHEDULE_13G_X02/primary_doc.xml",
            "isXBRL": 0,
        }
        for key in recent:
            recent[key].append(record[key])

    found = select_filing_candidates(
        adjusted, cik="0001456346", as_of_date="2026-11-05",
    )
    assert {item.accession_number for item in found} == {
        "0001104659-26-094181",
        "0001104659-26-033333",
        "0001104659-26-099999",
    }
    assert all(item.form in ("6-K", "40-F") for item in found)
