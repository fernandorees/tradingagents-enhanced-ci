"""Parse numeric facts from a *retrieved* SEC XBRL XML instance without I/O.

This is intentionally not a filing finder or a complete financial-statement
adapter. Callers must separately verify the SEC accession, document identity,
filing/publication date and historical as-of eligibility. No Inline XBRL HTML,
company-extension interpretation or derived quarter calculations are attempted.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
from xml.etree import ElementTree as ET

_XBRL = "{http://www.xbrl.org/2003/instance}"
_XSI_NIL = "{http://www.w3.org/2001/XMLSchema-instance}nil"
_ISO4217 = "http://www.xbrl.org/2003/iso4217"
_XBRL_NS = "http://www.xbrl.org/2003/instance"
_CIK_SCHEMES = {"http://www.sec.gov/CIK", "https://www.sec.gov/CIK"}
_MAX_XML_BYTES = 20_000_000
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_DECIMALS = re.compile(r"(?:[+-]?[0-9]+|INF)\Z")
_MAX_ABS_DECIMALS = 1000
_TAXONOMY = {
    "ifrs-full": re.compile(r"https?://xbrl\.ifrs\.org/taxonomy/\d{4}-\d{2}-\d{2}/ifrs-full\Z"),
    "us-gaap": re.compile(r"https?://fasb\.org/us-gaap/\d{4}\Z"),
}


class XbrlInstanceError(ValueError):
    """Invalid or ambiguous instance data; do not silently report a fact."""


@dataclass(frozen=True)
class XbrlFact:
    namespace: str
    concept: str
    start: str | None
    end: str
    unit: str
    value: Decimal
    context_id: str
    dimensional: bool
    decimals: str | None = None


def _date(text: str | None, label: str) -> str:
    if not isinstance(text, str) or not _DATE.fullmatch(text):
        raise XbrlInstanceError(f"Invalid XBRL {label}: {text!r}")
    try:
        date.fromisoformat(text)
    except ValueError as exc:
        raise XbrlInstanceError(f"Invalid XBRL {label}: {text!r}") from exc
    return text


def _accuracy(value: str | None) -> str | None:
    """Retain declared decimals; reject invalid or impractically large scales."""
    if value is None:
        return None  # Older fixtures without decimals remain parseable.
    if not _DECIMALS.fullmatch(value):
        raise XbrlInstanceError(f"Invalid XBRL decimals attribute: {value!r}")
    if value != "INF":
        magnitude = value.lstrip("+-").lstrip("0") or "0"
        if len(magnitude) > 4 or int(magnitude) > _MAX_ABS_DECIMALS:
            raise XbrlInstanceError("XBRL decimals attribute exceeds supported range")
    return value


def _cik(value: str) -> str:
    if not isinstance(value, str) or len(value) > 10 or not value.isascii() or not value.isdecimal():
        raise XbrlInstanceError("Invalid XBRL SEC CIK")
    number = int(value)
    if not 0 < number < 10**10:
        raise XbrlInstanceError("Invalid XBRL SEC CIK")
    return f"{number:010d}"


def _measure(element: ET.Element | None, namespaces: dict[str, str]) -> str | None:
    if element is None or not element.text:
        return None
    qname = element.text.strip()
    prefix, separator, local = qname.partition(":")
    if not separator:
        return None
    uri = namespaces.get(prefix)
    if uri == _ISO4217 and re.fullmatch(r"[A-Z]{3}", local):
        return local
    if uri == _XBRL_NS and local in ("shares", "pure"):
        return local
    return None


def _unit(element: ET.Element, namespaces: dict[str, str]) -> str | None:
    division = element.find(f"{_XBRL}divide")
    if division is None:
        measures = element.findall(f"{_XBRL}measure")
        return _measure(measures[0], namespaces) if len(measures) == 1 else None
    num = division.findall(f"{_XBRL}unitNumerator/{_XBRL}measure")
    den = division.findall(f"{_XBRL}unitDenominator/{_XBRL}measure")
    if len(num) != 1 or len(den) != 1:
        return None
    numerator, denominator = _measure(num[0], namespaces), _measure(den[0], namespaces)
    if numerator and denominator:
        return f"{numerator}/{denominator}"
    return None


def _context(element: ET.Element, expected_cik: str) -> tuple[str | None, str, bool]:
    identifier = element.find(f"{_XBRL}entity/{_XBRL}identifier")
    if identifier is None or identifier.get("scheme") not in _CIK_SCHEMES:
        raise XbrlInstanceError("XBRL entity identifier lacks a recognized SEC CIK scheme")
    if _cik((identifier.text or "").strip()) != expected_cik:
        raise XbrlInstanceError("XBRL context SEC CIK does not match requested issuer")
    period = element.find(f"{_XBRL}period")
    if period is None:
        raise XbrlInstanceError("XBRL context missing period")
    for name in ("instant", "startDate", "endDate"):
        if len(period.findall(f"{_XBRL}{name}")) > 1:
            raise XbrlInstanceError("XBRL context has duplicate period elements")
    if period.find(f"{_XBRL}forever") is not None:
        raise XbrlInstanceError("XBRL context period uses unsupported forever")
    instant = period.find(f"{_XBRL}instant")
    start = period.find(f"{_XBRL}startDate")
    end = period.find(f"{_XBRL}endDate")
    if instant is not None and start is None and end is None:
        begin = None
        finish = _date(instant.text, "instant")
    elif instant is None and start is not None and end is not None:
        begin = _date(start.text, "start date")
        finish = _date(end.text, "end date")
        if begin > finish:
            raise XbrlInstanceError("XBRL duration must not end before it starts")
    else:
        raise XbrlInstanceError("XBRL context has an ambiguous or incomplete period")
    entity = element.find(f"{_XBRL}entity")
    dimensional = (entity is not None and entity.find(f"{_XBRL}segment") is not None) or (
        element.find(f"{_XBRL}scenario") is not None
    )
    return begin, finish, dimensional


def parse_sec_xbrl_instance(document: bytes, *, cik: str) -> tuple[XbrlFact, ...]:
    """Return numeric facts with exact periods and units from XML, never fetch.

    Every context must identify the requested SEC CIK. Monetary units are
    resolved by their *namespace URIs*, not assumed prefix spellings. Unknown
    units are omitted rather than treated as USD. Segment/scenario facts are
    retained but flagged, and the selector below excludes them by default.
    """
    if not isinstance(document, bytes) or len(document) > _MAX_XML_BYTES:
        raise XbrlInstanceError("XBRL input must be bytes of an acceptable size")
    if b"<!DOCTYPE" in document.upper() or b"<!ENTITY" in document.upper():
        raise XbrlInstanceError("XBRL document type/entity declarations are disallowed")
    expected_cik = _cik(cik)
    namespaces: dict[str, str] = {}
    try:
        parsed = ET.iterparse(io.BytesIO(document), events=("start-ns", "end"))
        for event, item in parsed:
            if event == "start-ns":
                prefix, uri = item
                if prefix in namespaces and namespaces[prefix] != uri:
                    raise XbrlInstanceError("XBRL namespace prefix changes meaning within document")
                namespaces[prefix] = uri
        root = parsed.root
    except ET.ParseError as exc:
        raise XbrlInstanceError("Malformed XBRL instance XML") from exc
    if root is None or root.tag != f"{_XBRL}xbrl":
        raise XbrlInstanceError("Not an XBRL XML instance (Inline XBRL is unsupported)")
    contexts: dict[str, tuple[str | None, str, bool]] = {}
    for element in root.findall(f"{_XBRL}context"):
        identifier = element.get("id")
        if not identifier or identifier in contexts:
            raise XbrlInstanceError("XBRL context ID missing or duplicated")
        contexts[identifier] = _context(element, expected_cik)
    units: dict[str, str | None] = {}
    for element in root.findall(f"{_XBRL}unit"):
        identifier = element.get("id")
        if not identifier or identifier in units:
            raise XbrlInstanceError("XBRL unit ID missing or duplicated")
        units[identifier] = _unit(element, namespaces)
    facts: list[XbrlFact] = []
    for element in root:
        context_id = element.get("contextRef")
        if context_id is None or element.get(_XSI_NIL) in ("true", "1"):
            continue
        if context_id not in contexts:
            raise XbrlInstanceError(f"XBRL fact references unknown context {context_id!r}")
        unit_id = element.get("unitRef")
        if unit_id is None:
            continue  # textual/non-numeric item
        if unit_id not in units:
            raise XbrlInstanceError(f"XBRL fact references unknown unit {unit_id!r}")
        unit = units[unit_id]
        if unit is None:
            continue  # not a recognized monetary, share or ratio unit
        raw = (element.text or "").strip()
        try:
            value = Decimal(raw)
        except InvalidOperation as exc:
            raise XbrlInstanceError("Non-numeric XBRL fact with a recognized unit") from exc
        if not value.is_finite():
            raise XbrlInstanceError("Non-finite XBRL numeric fact")
        if not element.tag.startswith("{"):
            continue
        namespace, concept = element.tag[1:].split("}", 1)
        start, end, dimensional = contexts[context_id]
        decimals = _accuracy(element.get("decimals"))
        facts.append(
            XbrlFact(namespace, concept, start, end, unit, value, context_id, dimensional, decimals)
        )
    return tuple(facts)


def select_consolidated_fact(
    facts: tuple[XbrlFact, ...], *, taxonomy: str, concept: str,
    start: str | None, end: str, unit: str,
) -> Decimal | None:
    """Select exactly one standard-taxonomy fact, never sum or derive periods.

    Identical duplicates are harmless. Differently rounded duplicates are
    reconciled only when their declared accuracy intervals are consistent;
    unknown precision or genuine conflicts fail closed. Company extensions
    and dimensional contexts are not silently substituted.
    """
    pattern = _TAXONOMY.get(taxonomy)
    if pattern is None:
        raise ValueError(f"Unsupported XBRL taxonomy: {taxonomy}")
    _date(end, "requested end date")
    if start is not None:
        _date(start, "requested start date")
    candidates = [
        fact for fact in facts
        if not fact.dimensional and pattern.fullmatch(fact.namespace)
        and fact.concept == concept and fact.start == start
        and fact.end == end and fact.unit == unit
    ]
    if not candidates:
        return None
    values = {fact.value for fact in candidates}
    if len(values) == 1:
        return candidates[0].value

    conflict = f"Conflicting consolidated XBRL facts for {taxonomy}:{concept}"
    # Without stated accuracy, unequal values cannot safely be reconciled.
    if any(fact.decimals is None for fact in candidates):
        raise XbrlInstanceError(conflict)

    def rank(decimals: str) -> tuple[int, int]:
        return (1, 0) if decimals == "INF" else (0, int(decimals))

    # Facts with the same stated accuracy must report identical values,
    # including when their closed accuracy ranges happen to touch.
    by_accuracy: dict[tuple[int, int], set[Decimal]] = {}
    for fact in candidates:
        by_accuracy.setdefault(rank(fact.decimals), set()).add(fact.value)
    if any(len(group) != 1 for group in by_accuracy.values()):
        raise XbrlInstanceError(conflict)

    # XBRL consistent duplicates have a nonempty common intersection of
    # accuracy intervals. Evaluate *all* intervals, not just pairwise overlaps.
    with localcontext() as context:
        context.prec = max(
            28,
            max(len(fact.value.as_tuple().digits) for fact in candidates)
            + max(abs(int(fact.decimals)) if fact.decimals != "INF" else 0 for fact in candidates)
            + 4,
        )
        bounds = []
        for fact in candidates:
            radius = (
                Decimal(0) if fact.decimals == "INF"
                else Decimal(f"5e{-int(fact.decimals) - 1}")
            )
            bounds.append((fact.value - radius, fact.value + radius))
        if max(low for low, _ in bounds) > min(high for _, high in bounds):
            raise XbrlInstanceError(conflict)

    # Prefer the most accurately reported value; no averaging or summing.
    best_rank = max(by_accuracy)
    return next(iter(by_accuracy[best_rank]))
