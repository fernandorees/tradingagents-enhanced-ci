"""Bind an offline SEC XBRL instance to eligible filing and archive metadata.

No network access occurs here. Callers must retrieve SEC submissions, the exact
accession's archive index, and its XML bytes separately. An archive membership
check is *not* a cryptographic proof of document authenticity or a guarantee
that a filing's statements are complete. Unsupported Inline XBRL remains out
of scope, and no metrics or accounting periods are inferred.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from tradingagents.dataflows.vendors.sec_filing_discovery import select_filing_candidates
from tradingagents.dataflows.vendors.sec_xbrl_instance import XbrlFact, parse_sec_xbrl_instance

_XML_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+\-]*\.xml\Z", re.IGNORECASE)


class SecFiledXbrlError(ValueError):
    """Filing ineligible, archive document unverified, or no usable XML facts."""


@dataclass(frozen=True)
class SecFiledXbrl:
    """Parsed numeric facts plus the specific source filing's identifiers."""

    cik: str
    accession_number: str
    form: str
    filing_date: str
    accepted_at: str | None
    document_name: str
    document_url: str
    facts: tuple[XbrlFact, ...]


def parse_filed_xbrl(
    submissions: Mapping[str, Any],
    archive_index: Mapping[str, Any],
    document: bytes,
    *,
    cik: str,
    as_of_date: str,
    accession_number: str,
    instance_name: str,
    historical_files: Mapping[str, Any] | None = None,
) -> SecFiledXbrl:
    """Bind one explicitly identified XML file to one eligible SEC accession.

    The full accession must have passed submissions/acceptance date gating.
    Its archive index must name the same directory and explicitly list the
    chosen XML file exactly once. This never guesses the XML filename from the
    primary HTML filename, and never treats SEC ``isXBRL`` as proof of data.

    The caller must obtain the submitted JSON/index/XML from a trustworthy SEC
    source; arbitrary passed-in JSON/bytes are not independently authenticated.
    Historical files, when supplied, are validated by filing discovery.
    """
    candidates = select_filing_candidates(
        submissions, cik=cik, as_of_date=as_of_date, historical_files=historical_files
    )
    candidate = next(
        (item for item in candidates if item.accession_number == accession_number), None
    )
    if candidate is None:
        raise SecFiledXbrlError("SEC accession is not eligible as of requested date")
    if not isinstance(instance_name, str) or not _XML_NAME.fullmatch(instance_name):
        raise SecFiledXbrlError("Unsafe or unsupported SEC XBRL instance filename")

    directory = archive_index.get("directory") if isinstance(archive_index, Mapping) else None
    if not isinstance(directory, Mapping):
        raise SecFiledXbrlError("SEC accession archive index missing directory")
    expected_path = (
        f"/Archives/edgar/data/{int(candidate.cik)}/"
        f"{candidate.accession_number.replace('-', '')}"
    )
    actual_path = directory.get("name")
    if not isinstance(actual_path, str) or actual_path.rstrip("/") != expected_path:
        raise SecFiledXbrlError("SEC archive index belongs to a different accession")
    items = directory.get("item")
    if not isinstance(items, list):
        raise SecFiledXbrlError("SEC archive index has no document listing")
    matches = [
        item for item in items
        if isinstance(item, Mapping) and item.get("name") == instance_name
    ]
    if len(matches) != 1:
        raise SecFiledXbrlError("SEC XML document not uniquely listed in accession archive")

    facts = parse_sec_xbrl_instance(document, cik=candidate.cik)
    if not facts:
        raise SecFiledXbrlError("SEC XBRL instance has no supported numeric facts")
    return SecFiledXbrl(
        cik=candidate.cik,
        accession_number=candidate.accession_number,
        form=candidate.form,
        filing_date=candidate.filing_date,
        accepted_at=candidate.accepted_at,
        document_name=instance_name,
        document_url=candidate.archive_directory + instance_name,
        facts=facts,
    )
