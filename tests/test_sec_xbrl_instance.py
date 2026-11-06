"""Offline synthetic XBRL fixture tests; no SEC requests and no models.

These fixtures verify parsing rules, not any company's reported financial
figures. Live SEC provenance and filed-date gating belong in later integration.
"""

from __future__ import annotations

from decimal import Decimal
from xml.etree import ElementTree as ET

import pytest

from tradingagents.dataflows.vendors.sec_xbrl_instance import (
    XbrlInstanceError,
    parse_sec_xbrl_instance,
    select_consolidated_fact,
)

XBRL_URI = "http://www.xbrl.org/2003/instance"
XBRL = f"{{{XBRL_URI}}}"
IFRS_URI = "https://xbrl.ifrs.org/taxonomy/2025-03-27/ifrs-full"
GAAP_URI = "http://fasb.org/us-gaap/2025"
ISO_URI = "http://www.xbrl.org/2003/iso4217"
ET.register_namespace("xbrli", XBRL_URI)
ET.register_namespace("ifrs", IFRS_URI)
ET.register_namespace("gaap", GAAP_URI)


def _instance():
    root = ET.Element(f"{XBRL}xbrl", {"xmlns:iso4217": ISO_URI})
    for cid, start, dimensional in (
        ("q2", "2026-04-01", False),
        ("h1", "2026-01-01", False),
        ("bs", None, False),
        ("segment", "2026-04-01", True),
    ):
        context = ET.SubElement(root, f"{XBRL}context", id=cid)
        entity = ET.SubElement(context, f"{XBRL}entity")
        ET.SubElement(entity, f"{XBRL}identifier", scheme="http://www.sec.gov/CIK").text = "0001456346"
        if dimensional:
            ET.SubElement(entity, f"{XBRL}segment")
        period = ET.SubElement(context, f"{XBRL}period")
        if start is None:
            ET.SubElement(period, f"{XBRL}instant").text = "2026-06-30"
        else:
            ET.SubElement(period, f"{XBRL}startDate").text = start
            ET.SubElement(period, f"{XBRL}endDate").text = "2026-06-30"
    for identifier, measure in (("usd", "iso4217:USD"), ("eur", "iso4217:EUR"), ("shares", "xbrli:shares")):
        unit = ET.SubElement(root, f"{XBRL}unit", id=identifier)
        ET.SubElement(unit, f"{XBRL}measure").text = measure
    eps = ET.SubElement(root, f"{XBRL}unit", id="eps")
    division = ET.SubElement(eps, f"{XBRL}divide")
    num = ET.SubElement(division, f"{XBRL}unitNumerator")
    ET.SubElement(num, f"{XBRL}measure").text = "iso4217:USD"
    den = ET.SubElement(division, f"{XBRL}unitDenominator")
    ET.SubElement(den, f"{XBRL}measure").text = "xbrli:shares"
    return root


def _add(root, name, *, context="q2", unit="usd", value="580900000", namespace=IFRS_URI, nil=False):
    attributes = {"contextRef": context, "unitRef": unit}
    if nil:
        attributes["{http://www.w3.org/2001/XMLSchema-instance}nil"] = "true"
    fact = ET.SubElement(root, f"{{{namespace}}}{name}", attributes)
    fact.text = value
    return fact


def _read(root):
    return parse_sec_xbrl_instance(ET.tostring(root), cik="1456346")


def _value(facts, *, concept="RevenueFromContractsWithCustomers", start="2026-04-01", end="2026-06-30", unit="USD", taxonomy="ifrs-full"):
    return select_consolidated_fact(
        facts, taxonomy=taxonomy, concept=concept, start=start, end=end, unit=unit
    )


@pytest.mark.unit
def test_ifrs_quarter_and_year_to_date_are_distinct_exact_periods():
    root = _instance()
    _add(root, "RevenueFromContractsWithCustomers")
    _add(root, "RevenueFromContractsWithCustomers", context="h1", value="1231600000")
    facts = _read(root)
    assert _value(facts) == Decimal("580900000")
    assert _value(facts, start="2026-01-01") == Decimal("1231600000")
    assert _value(facts, start="2026-04-02") is None  # never guess a nearby span


@pytest.mark.unit
def test_balance_sheet_instant_is_not_interchanged_with_duration():
    root = _instance()
    _add(root, "Assets", context="bs", value="8922200000")
    facts = _read(root)
    assert _value(facts, concept="Assets", start=None) == Decimal("8922200000")
    assert _value(facts, concept="Assets") is None


@pytest.mark.unit
def test_segment_fact_never_replaces_consolidated_figure():
    root = _instance()
    _add(root, "RevenueFromContractsWithCustomers", value="580900000")
    _add(root, "RevenueFromContractsWithCustomers", context="segment", value="999999999")
    assert _value(_read(root)) == Decimal("580900000")


@pytest.mark.unit
def test_segment_only_is_explicitly_absent_from_consolidated_selection():
    root = _instance()
    _add(root, "RevenueFromContractsWithCustomers", context="segment")
    assert _value(_read(root)) is None


@pytest.mark.unit
def test_currency_is_exact_and_no_eur_to_usd_conversion_is_inferred():
    root = _instance()
    _add(root, "RevenueFromContractsWithCustomers", unit="eur")
    facts = _read(root)
    assert _value(facts) is None
    assert _value(facts, unit="EUR") == Decimal("580900000")


@pytest.mark.unit
def test_diluted_eps_unit_is_not_millions_or_mistaken_for_shares():
    root = _instance()
    _add(root, "DilutedEarningsLossPerShare", unit="eps", value="1.83")
    facts = _read(root)
    assert _value(facts, concept="DilutedEarningsLossPerShare", unit="USD/shares") == Decimal("1.83")
    assert _value(facts, concept="DilutedEarningsLossPerShare", unit="USD") is None


@pytest.mark.unit
def test_us_gaap_and_ifrs_tag_names_are_not_interchangeable():
    root = _instance()
    _add(root, "Assets", context="bs", value="100", namespace=GAAP_URI)
    _add(root, "Assets", context="bs", value="200", namespace=IFRS_URI)
    facts = _read(root)
    assert _value(facts, concept="Assets", start=None, taxonomy="us-gaap") == Decimal("100")
    assert _value(facts, concept="Assets", start=None, taxonomy="ifrs-full") == Decimal("200")


@pytest.mark.unit
def test_company_extension_tag_does_not_impersonate_standard_taxonomy():
    root = _instance()
    _add(root, "RevenueFromContractsWithCustomers", namespace="https://example.test/company/ifrs-full")
    assert _value(_read(root)) is None


@pytest.mark.unit
def test_context_cik_mismatch_fails_closed():
    root = _instance()
    identifier = root.find(f"{XBRL}context/{XBRL}entity/{XBRL}identifier")
    identifier.text = "0000320193"
    with pytest.raises(XbrlInstanceError, match="CIK does not match"):
        _read(root)


@pytest.mark.unit
def test_conflicting_duplicates_raise_instead_of_choosing_arbitrarily():
    root = _instance()
    _add(root, "RevenueFromContractsWithCustomers", value="580900000")
    _add(root, "RevenueFromContractsWithCustomers", value="999900000")
    with pytest.raises(XbrlInstanceError, match="Conflicting"):
        _value(_read(root))


@pytest.mark.unit
def test_identical_duplicate_numeric_facts_are_ok():
    root = _instance()
    _add(root, "RevenueFromContractsWithCustomers")
    _add(root, "RevenueFromContractsWithCustomers", value="580900000.00")
    assert _value(_read(root)) == Decimal("580900000")


@pytest.mark.unit
def test_nil_facts_are_not_treated_as_zero():
    root = _instance()
    _add(root, "RevenueFromContractsWithCustomers", nil=True)
    assert _value(_read(root)) is None


@pytest.mark.unit
def test_single_day_duration_in_dimensional_context_is_valid():
    # Reproduces SAP 2024 Form 20-F's Qualtrics dimensional context.
    root = _instance()
    segment = next(context for context in root.findall(f"{XBRL}context") if context.get("id") == "segment")
    period = segment.find(f"{XBRL}period")
    period.find(f"{XBRL}startDate").text = "2023-03-13"
    period.find(f"{XBRL}endDate").text = "2023-03-13"
    _add(root, "RevenueFromContractsWithCustomers", context="segment", unit="eur", value="47")
    facts = _read(root)
    assert len(facts) == 1
    assert (facts[0].start, facts[0].end, facts[0].dimensional) == (
        "2023-03-13", "2023-03-13", True
    )
    # A dimensional fact must never be promoted to a consolidated value.
    assert _value(facts, start="2023-03-13", end="2023-03-13", unit="EUR") is None


@pytest.mark.unit
def test_reverse_ordered_duration_is_still_rejected():
    root = _instance()
    period = root.find(f"{XBRL}context/{XBRL}period")
    period.find(f"{XBRL}startDate").text = "2026-07-01"
    with pytest.raises(XbrlInstanceError, match="XBRL duration"):
        _read(root)


@pytest.mark.unit
def test_invalid_period_and_unknown_context_fail_closed():
    root = _instance()
    start = root.find(f"{XBRL}context/{XBRL}period/{XBRL}startDate")
    start.text = "2026-99-99"
    with pytest.raises(XbrlInstanceError, match="Invalid XBRL start date"):
        _read(root)
    root = _instance()
    _add(root, "Assets", context="not-defined")
    with pytest.raises(XbrlInstanceError, match="unknown context"):
        _read(root)


@pytest.mark.unit
def test_doctype_and_inline_html_are_rejected():
    with pytest.raises(XbrlInstanceError, match="declarations are disallowed"):
        parse_sec_xbrl_instance(b'<!DOCTYPE xbrl [<!ENTITY a "b">]><xbrl/>', cik="1456346")
    with pytest.raises(XbrlInstanceError, match="Not an XBRL XML instance"):
        parse_sec_xbrl_instance(b"<html><body>not an instance</body></html>", cik="1456346")


@pytest.mark.unit
def test_source_filing_date_is_not_inferred_from_xbrl_period():
    root = _instance()
    _add(root, "RevenueFromContractsWithCustomers")
    facts = _read(root)
    assert facts[0].end == "2026-06-30"
    assert not hasattr(facts[0], "filing_date")  # verify this separately from SEC metadata


@pytest.mark.unit
def test_currency_qname_alias_is_resolved_by_namespace_uri_not_prefix_name():
    root = _instance()
    root.set("xmlns:money", ISO_URI)
    usd = next(x for x in root.findall(f"{XBRL}unit") if x.get("id") == "usd")
    usd.find(f"{XBRL}measure").text = "money:USD"
    _add(root, "RevenueFromContractsWithCustomers")
    assert _value(_read(root)) == Decimal("580900000")


@pytest.mark.unit
def test_untrusted_currency_prefix_is_not_silently_interpreted_as_usd():
    root = _instance()
    root.set("xmlns:untrusted", "https://example.test/not-iso4217")
    usd = next(x for x in root.findall(f"{XBRL}unit") if x.get("id") == "usd")
    usd.find(f"{XBRL}measure").text = "untrusted:USD"
    _add(root, "RevenueFromContractsWithCustomers")
    assert _value(_read(root)) is None


@pytest.mark.unit
def test_duplicate_start_date_is_rejected():
    root = _instance()
    period = root.find(f'{XBRL}context/{XBRL}period')
    ET.SubElement(period, f'{XBRL}startDate').text = '2026-02-01'
    _add(root, 'RevenueFromContractsWithCustomers')
    with pytest.raises(XbrlInstanceError, match='period'):
        _read(root)


@pytest.mark.unit
@pytest.mark.parametrize('context_id,element', [('bs','instant'),('q2','endDate')])
def test_duplicate_instant_or_end_date_is_rejected(context_id, element):
    root = _instance()
    context = next(c for c in root.findall(f'{XBRL}context') if c.get('id') == context_id)
    period = context.find(f'{XBRL}period')
    ET.SubElement(period, f'{XBRL}{element}').text = '2026-05-31'
    _add(root, 'Assets', context=context_id)
    with pytest.raises(XbrlInstanceError, match='duplicate period'):
        _read(root)


@pytest.mark.unit
def test_instant_and_forever_mixture_is_rejected():
    root = _instance()
    context = next(c for c in root.findall(f'{XBRL}context') if c.get('id') == 'bs')
    period = context.find(f'{XBRL}period')
    ET.SubElement(period, f'{XBRL}forever')
    _add(root, 'Assets', context='bs')
    with pytest.raises(XbrlInstanceError, match='period'):
        _read(root)


@pytest.mark.unit
@pytest.mark.parametrize('context_id,standalone', [('bs',True),('q2',False)])
def test_unsupported_forever_period_is_rejected(context_id, standalone):
    root = _instance()
    context = next(c for c in root.findall(f'{XBRL}context') if c.get('id') == context_id)
    period = context.find(f'{XBRL}period')
    if standalone:
        period.remove(period.find(f'{XBRL}instant'))
    ET.SubElement(period, f'{XBRL}forever')
    _add(root, 'Assets', context=context_id)
    with pytest.raises(XbrlInstanceError, match='forever'):
        _read(root)
