"""Offline metadata-to-XBRL integration: fabricated SEC JSON and XML only.

A passing case verifies eligibility and in-memory parsing, not SEC source
identity, publication-time accuracy, completeness, or accounting correctness.
"""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from xml.etree import ElementTree as ET

import pytest

from tradingagents.dataflows.vendors.sec_filed_xbrl import SecFiledXbrlError, parse_filed_xbrl
from tradingagents.dataflows.vendors.sec_xbrl_instance import (
    XbrlInstanceError,
    select_consolidated_fact,
)

XBRL = "{http://www.xbrl.org/2003/instance}"
IFRS = "{https://xbrl.ifrs.org/taxonomy/2025-03-27/ifrs-full}"
GAAP = "{http://fasb.org/us-gaap/2025}"
ISO = "http://www.xbrl.org/2003/iso4217"
FNV_ACC = "0001104659-26-094181"
FNV_INSTANCE = "fnv-20260630x6k_htm.xml"


@pytest.fixture
def submission():
    return {"cik": 1456346, "filings": {"recent": {
        "form": ["6-K", "40-F"],
        "accessionNumber": [FNV_ACC, "0001104659-26-033333"],
        "filingDate": ["2026-08-12", "2026-03-19"],
        "acceptanceDateTime": ["2026-08-12T17:23:00", "2026-03-19T09:45:00"],
        "reportDate": ["2026-06-30", "2025-12-31"],
        "primaryDocument": ["fnv-6k.htm", "fnv-40f.htm"],
        "isXBRL": [0, 1],
    }}}


@pytest.fixture
def index():
    return {"directory": {
        "name": "/Archives/edgar/data/1456346/000110465926094181",
        "item": [{"name": "FilingSummary.xml", "type": "text/xml"},
                 {"name": FNV_INSTANCE, "type": "text/xml"}],
    }}


def _xml(*, cik="0001456346", namespace=IFRS, include_revenue=True, conflicting=False):
    root = ET.Element(XBRL + "xbrl", {"xmlns:iso4217": ISO})
    unit = ET.SubElement(root, XBRL + "unit", id="usd")
    ET.SubElement(unit, XBRL + "measure").text = "iso4217:USD"
    for ref, start in (("q2", "2026-04-01"), ("h1", "2026-01-01")):
        context = ET.SubElement(root, XBRL + "context", id=ref)
        entity = ET.SubElement(context, XBRL + "entity")
        ET.SubElement(entity, XBRL + "identifier", scheme="http://www.sec.gov/CIK").text = cik
        period = ET.SubElement(context, XBRL + "period")
        ET.SubElement(period, XBRL + "startDate").text = start
        ET.SubElement(period, XBRL + "endDate").text = "2026-06-30"
    if include_revenue:
        concept = "RevenueFromContractsWithCustomers" if namespace == IFRS else "Revenues"
        for context_ref, value in (("q2", "580900000"), ("h1", "1231600000")):
            ET.SubElement(
                root, namespace + concept, contextRef=context_ref, unitRef="usd"
            ).text = value
        if conflicting:
            ET.SubElement(
                root, namespace + concept, contextRef="q2", unitRef="usd"
            ).text = "999000000"
    return ET.tostring(root)


def _run(submission, index, content=None, **kwargs):
    return parse_filed_xbrl(
        submission, index, _xml() if content is None else content,
        cik=kwargs.pop("cik", "1456346"),
        as_of_date=kwargs.pop("as_of_date", "2026-08-12"),
        accession_number=kwargs.pop("accession_number", FNV_ACC),
        instance_name=kwargs.pop("instance_name", FNV_INSTANCE),
        **kwargs,
    )


@pytest.fixture
def historical_only_submission(submission):
    """Place an otherwise valid 2026 filing exclusively in a historical file."""
    adjusted = deepcopy(submission)
    recent = adjusted["filings"]["recent"]
    historical = {field: [values.pop(0)] for field, values in recent.items()}
    filename = "CIK0001456346-submissions-001.json"
    adjusted["filings"]["files"] = [{
        "name": filename,
        "filingFrom": "2026-08-12",
        "filingTo": "2026-08-12",
        "filingCount": 1,
    }]
    return adjusted, filename, historical


@pytest.mark.unit
def test_historical_only_filing_is_bound_to_original_accession_and_facts(
    historical_only_submission, index,
):
    submissions, filename, records = historical_only_submission
    # Without explicitly supplied historical metadata, the accession is ineligible.
    with pytest.raises(SecFiledXbrlError, match="not eligible"):
        _run(submissions, index)

    result = _run(submissions, index, historical_files={filename: records})
    assert (result.cik, result.form, result.accession_number) == (
        "0001456346", "6-K", FNV_ACC
    )
    assert result.filing_date == "2026-08-12"
    assert result.document_url.endswith("/000110465926094181/" + FNV_INSTANCE)
    assert select_consolidated_fact(
        result.facts,
        taxonomy="ifrs-full",
        concept="RevenueFromContractsWithCustomers",
        start="2026-04-01",
        end="2026-06-30",
        unit="USD",
    ) == Decimal("580900000")


@pytest.mark.unit
def test_historical_only_filing_not_eligible_before_publication_date(
    historical_only_submission, index,
):
    submissions, filename, records = historical_only_submission
    with pytest.raises(SecFiledXbrlError, match="not eligible"):
        _run(
            submissions, index,
            historical_files={filename: records},
            as_of_date="2026-08-11",
        )


@pytest.mark.unit
@pytest.mark.parametrize("fault, expected", [
    ("missing", "incomplete or unlisted"),
    ("unlisted", "incomplete or unlisted"),
    ("count", "filingCount mismatch"),
    ("range", "outside manifest date range"),
    ("conflict", "Conflicting SEC metadata"),
])
def test_filed_xbrl_historical_metadata_fails_closed(
    historical_only_submission, index, fault, expected,
):
    submissions, filename, records = deepcopy(historical_only_submission)
    supplied = {filename: records}
    manifest = submissions["filings"]["files"][0]
    if fault == "missing":
        supplied.clear()
    elif fault == "unlisted":
        supplied["not-in-manifest.json"] = records
    elif fault == "count":
        manifest["filingCount"] = 2
    elif fault == "range":
        records["filingDate"][0] = "2026-08-13"
    else:
        recent = submissions["filings"]["recent"]
        records["accessionNumber"][0] = recent["accessionNumber"][0]
        records["filingDate"][0] = recent["filingDate"][0]
        manifest["filingFrom"] = records["filingDate"][0]

    with pytest.raises(ValueError, match=expected):
        _run(submissions, index, historical_files=supplied)


@pytest.mark.unit
def test_q2_and_h1_are_bound_to_eligible_accession_and_real_document_name(submission, index):
    result = _run(submission, index)
    assert (result.cik, result.form, result.accession_number) == ("0001456346", "6-K", FNV_ACC)
    assert result.filing_date == "2026-08-12"
    assert result.accepted_at == "2026-08-12T17:23:00"
    assert result.document_url == (
        "https://www.sec.gov/Archives/edgar/data/1456346/000110465926094181/"
        + FNV_INSTANCE
    )
    values = result.facts
    assert select_consolidated_fact(
        values, taxonomy="ifrs-full", concept="RevenueFromContractsWithCustomers",
        start="2026-04-01", end="2026-06-30", unit="USD",
    ) == Decimal("580900000")
    assert select_consolidated_fact(
        values, taxonomy="ifrs-full", concept="RevenueFromContractsWithCustomers",
        start="2026-01-01", end="2026-06-30", unit="USD",
    ) == Decimal("1231600000")


@pytest.mark.unit
def test_filing_not_available_day_before_is_not_used(submission, index):
    with pytest.raises(SecFiledXbrlError, match="not eligible"):
        _run(submission, index, as_of_date="2026-08-11")


@pytest.mark.unit
def test_acceptance_after_analysis_date_blocks_already_dated_filing(submission, index):
    changed = deepcopy(submission)
    changed["filings"]["recent"]["acceptanceDateTime"][0] = "2026-08-13T00:01:00"
    with pytest.raises(SecFiledXbrlError, match="not eligible"):
        _run(changed, index)


@pytest.mark.unit
def test_different_cik_rejected_before_xml_read(submission, index):
    with pytest.raises(ValueError, match="CIK does not match"):
        _run(submission, index, cik="320193")


@pytest.mark.unit
@pytest.mark.parametrize(
    "name", ["../other.xml", "subdir/file.xml", "unsafe.xml?x=1", "file.htm", ""]
)
def test_unsafe_instance_names_not_accepted(submission, index, name):
    with pytest.raises(SecFiledXbrlError, match="Unsafe or unsupported"):
        _run(submission, index, instance_name=name)


@pytest.mark.unit
def test_unlisted_document_is_not_accepted(submission, index):
    with pytest.raises(SecFiledXbrlError, match="not uniquely listed"):
        _run(submission, index, instance_name="someother.xml")


@pytest.mark.unit
def test_duplicate_archive_entries_fail_closed(submission, index):
    index = deepcopy(index)
    index["directory"]["item"].append({"name": FNV_INSTANCE})
    with pytest.raises(SecFiledXbrlError, match="not uniquely listed"):
        _run(submission, index)


@pytest.mark.unit
def test_index_from_another_accession_is_rejected(submission, index):
    index = deepcopy(index)
    index["directory"]["name"] = "/Archives/edgar/data/1456346/000110465926000001"
    with pytest.raises(SecFiledXbrlError, match="different accession"):
        _run(submission, index)


@pytest.mark.unit
def test_wrong_cik_inside_instance_fails_after_filing_check(submission, index):
    with pytest.raises(XbrlInstanceError, match="CIK does not match"):
        _run(submission, index, _xml(cik="320193"))


@pytest.mark.unit
def test_bad_xml_is_not_silently_treated_as_usable_instance(submission, index):
    with pytest.raises(XbrlInstanceError, match="Malformed"):
        _run(submission, index, b"<xbrli:xbrl>")


@pytest.mark.unit
def test_instance_with_no_supported_numeric_facts_is_rejected(submission, index):
    with pytest.raises(SecFiledXbrlError, match="no supported numeric facts"):
        _run(submission, index, _xml(include_revenue=False))


@pytest.mark.unit
def test_conflicting_facts_are_still_rejected_by_selector(submission, index):
    result = _run(submission, index, _xml(conflicting=True))
    with pytest.raises(XbrlInstanceError, match="Conflicting"):
        select_consolidated_fact(
            result.facts, taxonomy="ifrs-full", concept="RevenueFromContractsWithCustomers",
            start="2026-04-01", end="2026-06-30", unit="USD",
        )


@pytest.mark.unit
def test_us_gaap_filing_uses_same_metadata_binding(submission, index):
    result = _run(submission, index, _xml(namespace=GAAP))
    assert select_consolidated_fact(
        result.facts, taxonomy="us-gaap", concept="Revenues",
        start="2026-04-01", end="2026-06-30", unit="USD",
    ) == Decimal("580900000")


@pytest.mark.unit
def test_isxbrl_flag_does_not_override_explicit_verified_xml(submission, index):
    assert submission["filings"]["recent"]["isXBRL"][0] == 0
    assert len(_run(submission, index).facts) == 2
