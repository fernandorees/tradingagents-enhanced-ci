"""Select SEC filing *candidates* using public submissions metadata, offline.

A candidate is not an assertion that the filing contains usable XBRL. The SEC
filing's file manifest and instance document must be discovered and validated
in a later step. This reader covers ``filings.recent`` only; older submissions
listed in ``filings.files`` require separate retrieval before completeness can
be claimed. No I/O or model calls occur here.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

_ACCEPTANCE_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})?\Z"
)
_ACCESSION_RE = re.compile(r"\d{10}-\d{2}-\d{6}\Z")
_DOCUMENT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+\-]*\Z")
_SUPPORTED_FORMS = ("6-K", "20-F", "40-F", "10-Q", "10-K")
_REQUIRED_COLUMNS = ("form", "accessionNumber", "filingDate", "primaryDocument")


@dataclass(frozen=True)
class SecFilingCandidate:
    cik: str
    accession_number: str
    form: str
    filing_date: str
    report_date: str | None
    accepted_at: str | None
    primary_document: str
    archive_directory: str


def _cik(value: Any) -> str:
    if isinstance(value, bool) or not str(value).isdigit() or int(value) <= 0:
        raise ValueError("SEC CIK must be a positive decimal identifier")
    return f"{int(value):010d}"


def _date(value: str, field: str) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError(f"Invalid SEC {field}: {value!r}")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Invalid SEC {field}: {value!r}") from exc


def _acceptance_date(value: str) -> date:
    """Validate a complete ISO-like SEC acceptance time; preserve its source date.

    Daily gating does not establish the time zone or intraday availability.
    """
    if not isinstance(value, str) or not _ACCEPTANCE_RE.fullmatch(value):
        raise ValueError(f"Invalid SEC acceptance timestamp: {value!r}")
    try:
        return datetime.fromisoformat(value).date()
    except ValueError as exc:
        raise ValueError(f"Invalid SEC acceptance timestamp: {value!r}") from exc


def select_filing_candidates(
    submissions: Mapping[str, Any],
    *,
    cik: str,
    as_of_date: str,
    forms: tuple[str, ...] = _SUPPORTED_FORMS,
    historical_files: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[SecFilingCandidate]:
    """Return filing candidates eligible at daily ``as_of_date`` precision.

    With ``historical_files=None``, preserve the existing recent-only behavior.
    When historical files are supplied, require all filenames from the main
    ``filings.files`` manifest, and validate their bounds and counts. Neither
    the manifest nor caller-supplied records authenticate an SEC download.
    No files are read or fetched by this function.
    """
    cutoff = _date(as_of_date, "as-of date")
    normalized_cik = _cik(cik)
    if _cik(submissions.get("cik")) != normalized_cik:
        raise ValueError("SEC submissions CIK does not match requested CIK")

    filings = submissions.get("filings") or {}
    if not isinstance(filings, Mapping):
        raise ValueError("Invalid SEC filings section")
    recent = filings.get("recent") or {}
    if not isinstance(recent, Mapping):
        raise ValueError("Invalid SEC submissions.recent section")

    sections: list[
        tuple[str, Mapping[str, Any], tuple[date, date] | None, int | None]
    ] = [("submissions.recent", recent, None, None)]

    if historical_files is not None:
        if not isinstance(historical_files, Mapping):
            raise ValueError("SEC historical_files must be a filename-to-records mapping")
        manifest = filings.get("files")
        if not isinstance(manifest, list):
            raise ValueError("SEC filings.files manifest required for historical records")

        listed: dict[str, tuple[date, date, int]] = {}
        filename_pattern = re.compile(
            rf"CIK{normalized_cik}-submissions-[0-9]+[.]json\Z"
        )
        for descriptor in manifest:
            if not isinstance(descriptor, Mapping):
                raise ValueError("Invalid SEC historical manifest descriptor")
            name = descriptor.get("name")
            if not isinstance(name, str) or not filename_pattern.fullmatch(name):
                raise ValueError(f"Invalid SEC historical filename or CIK: {name!r}")
            if name in listed:
                raise ValueError(f"Duplicate SEC historical manifest filename: {name}")
            first = _date(descriptor.get("filingFrom"), "historical filingFrom")
            last = _date(descriptor.get("filingTo"), "historical filingTo")
            if first > last:
                raise ValueError(f"Invalid SEC historical filing date range: {name}")
            count = descriptor.get("filingCount")
            if type(count) is not int or count < 0:
                raise ValueError(f"Invalid SEC historical filingCount: {name}")
            listed[name] = (first, last, count)

        if set(historical_files) != set(listed):
            missing = sorted(set(listed) - set(historical_files))
            unlisted = sorted(set(historical_files) - set(listed))
            raise ValueError(
                f"SEC historical files incomplete or unlisted: "
                f"missing={missing}, unlisted={unlisted}"
            )
        for name, (first, last, expected_count) in listed.items():
            records = historical_files[name]
            if not isinstance(records, Mapping):
                raise ValueError(f"Invalid SEC historical records: {name}")
            sections.append((f"historical {name}", records, (first, last), expected_count))

    found: dict[str, SecFilingCandidate] = {}
    for source, records, bounds, expected_count in sections:
        form_column = records.get("form", [])
        if not isinstance(form_column, list):
            raise ValueError(f"SEC {source} missing or misaligned column: form")
        count = len(form_column)
        for name in _REQUIRED_COLUMNS:
            if not isinstance(records.get(name), list) or len(records[name]) != count:
                raise ValueError(f"SEC {source} missing or misaligned column: {name}")
        for name in ("acceptanceDateTime", "reportDate"):
            if name in records and (
                not isinstance(records[name], list) or len(records[name]) != count
            ):
                raise ValueError(f"SEC {source} misaligned column: {name}")
        if expected_count is not None and count != expected_count:
            raise ValueError(f"SEC historical filingCount mismatch: {source}")

        for index in range(count):
            form = records["form"][index]
            filing_date = records["filingDate"][index]
            # Validate all historical date bounds, including unsupported forms.
            if bounds is not None:
                actual_date = _date(filing_date, "filing date")
                if not bounds[0] <= actual_date <= bounds[1]:
                    raise ValueError(f"SEC historical filing outside manifest date range: {source}")
            if form not in forms:
                continue
            if _date(filing_date, "filing date") > cutoff:
                continue
            accepted_at = records.get("acceptanceDateTime", [None] * count)[index]
            if accepted_at is not None and _acceptance_date(accepted_at) > cutoff:
                continue
            accession = records["accessionNumber"][index]
            document = records["primaryDocument"][index]
            if not isinstance(accession, str) or not _ACCESSION_RE.fullmatch(accession):
                raise ValueError(f"Invalid SEC accession: {accession!r}")
            if not isinstance(document, str) or not _DOCUMENT_RE.fullmatch(document):
                raise ValueError(f"Unsafe SEC primary document: {document!r}")
            reported = records.get("reportDate", [None] * count)[index] or None
            if reported is not None:
                _date(reported, "report date")
            directory = (
                f"https://www.sec.gov/Archives/edgar/data/{int(normalized_cik)}/"
                f"{accession.replace('-', '')}/"
            )
            candidate = SecFilingCandidate(
                cik=normalized_cik,
                accession_number=accession,
                form=form,
                filing_date=filing_date,
                report_date=reported,
                accepted_at=accepted_at,
                primary_document=document,
                archive_directory=directory,
            )
            previous = found.get(accession)
            if previous is not None and previous != candidate:
                raise ValueError(f"Conflicting SEC metadata for accession {accession}")
            found[accession] = candidate

    return sorted(found.values(), key=lambda f: (f.filing_date, f.accession_number), reverse=True)
