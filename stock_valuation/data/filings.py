from __future__ import annotations

import re

import pandas as pd
import requests

# SEC requires a descriptive User-Agent identifying the requester on every
# call, or it 403s.
SEC_HEADERS = {"User-Agent": "stock-valuation-screener research@example.com"}
TICKER_CIK_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
FORM_TYPES = ("10-K", "10-Q")

# Company-authored disclosure sections only — the actual filed text, never a
# sell-side summary or news writeup of it. (start pattern, end pattern) —
# the end pattern bounds how far the section extends before the next Item.
_ITEM_PATTERNS = {
    "risk_factors": (r"item\s*1a\.?\s*risk\s*factors", r"item\s*1b\.?"),
    "mdna": (r"item\s*7\.?\s*management'?s\s*discussion", r"item\s*7a\.?"),
}


def fetch_cik_lookup() -> dict[str, int]:
    """Ticker -> CIK, needed to look up a company's filing history."""
    resp = requests.get(TICKER_CIK_URL, headers=SEC_HEADERS, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return {row["ticker"].upper(): row["cik_str"] for row in data.values()}


def fetch_filing_history(cik: int) -> pd.DataFrame:
    """As-filed 10-K/10-Q history for one company, oldest first."""
    resp = requests.get(SUBMISSIONS_URL.format(cik=cik), headers=SEC_HEADERS, timeout=15)
    resp.raise_for_status()
    recent = resp.json()["filings"]["recent"]
    df = pd.DataFrame(
        {
            "form": recent["form"],
            "filing_date": pd.to_datetime(recent["filingDate"]),
            "accession_number": recent["accessionNumber"],
            "primary_document": recent["primaryDocument"],
        }
    )
    return df[df["form"].isin(FORM_TYPES)].sort_values("filing_date").reset_index(drop=True)


def fetch_filing_text(cik: int, accession_number: str, primary_document: str) -> str:
    """Raw text of one filing's primary document, HTML tags stripped."""
    accession_no_dashes = accession_number.replace("-", "")
    url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_no_dashes}/{primary_document}"
    resp = requests.get(url, headers=SEC_HEADERS, timeout=30)
    resp.raise_for_status()
    return re.sub(r"<[^>]+>", " ", resp.text)


def extract_section(text: str, section: str) -> str:
    """Pull one named section ("risk_factors" or "mdna") out of filing text.

    Returns "" if the heading can't be found — older or nonstandard filings
    don't always use the exact "Item 1A." wording, and a missing section
    should degrade to "no text" rather than raise.
    """
    start_pat, end_pat = _ITEM_PATTERNS[section]
    start = re.search(start_pat, text, re.IGNORECASE)
    if not start:
        return ""
    remainder = text[start.end():]
    end = re.search(end_pat, remainder, re.IGNORECASE)
    body = remainder[: end.start()] if end else remainder[:20000]
    return re.sub(r"\s+", " ", body).strip()


def select_point_in_time_filing(filing_history: pd.DataFrame, as_of: pd.Timestamp) -> pd.Series | None:
    """Most recent filing dated strictly before `as_of`.

    Training a quarter's label on a filing that was only made public after
    that quarter's forward-return window began would leak future disclosure
    into the past — this always looks backward from `as_of`, never forward.
    """
    eligible = filing_history[filing_history["filing_date"] < as_of]
    if eligible.empty:
        return None
    return eligible.sort_values("filing_date").iloc[-1]
