from __future__ import annotations

from typing import Callable

import numpy as np
import pandas as pd

from stock_valuation.data.filings import select_point_in_time_filing


def build_filing_lookup(
    periods_by_ticker: pd.DataFrame, filing_histories: dict[str, pd.DataFrame]
) -> pd.DataFrame:
    """For each (ticker, period) row, find the filing that was actually
    public knowledge as of that period — point-in-time, no look-ahead.

    Rows with no eligible filing yet (e.g. a company's first few covered
    quarters, before any 10-K/10-Q predates them) get None/NaT.
    """
    rows = []
    for _, row in periods_by_ticker.iterrows():
        history = filing_histories.get(row["ticker"])
        filing = select_point_in_time_filing(history, row["period"]) if history is not None else None
        rows.append(
            {
                "ticker": row["ticker"],
                "period": row["period"],
                "accession_number": filing["accession_number"] if filing is not None else None,
                "primary_document": filing["primary_document"] if filing is not None else None,
                "filing_date": filing["filing_date"] if filing is not None else pd.NaT,
            }
        )
    return pd.DataFrame(rows)


def attach_text_embeddings(
    periods_by_ticker: pd.DataFrame,
    filing_lookup: pd.DataFrame,
    fetch_and_embed_filing: Callable[[str, str, str], np.ndarray],
    embedding_dim: int,
) -> pd.DataFrame:
    """Attach one embedding vector per (ticker, period) row.

    Many consecutive quarters share the same filing until the next one is
    filed, so each unique (ticker, accession_number) is only fetched and
    embedded once via `fetch_and_embed_filing`, then reused.
    """
    merged = periods_by_ticker.merge(filing_lookup, on=["ticker", "period"], how="left")
    cache: dict[tuple, np.ndarray] = {}
    vectors = []
    for _, row in merged.iterrows():
        if pd.isna(row["accession_number"]):
            vectors.append(np.zeros(embedding_dim))
            continue
        key = (row["ticker"], row["accession_number"])
        if key not in cache:
            cache[key] = fetch_and_embed_filing(row["ticker"], row["accession_number"], row["primary_document"])
        vectors.append(cache[key])

    emb_matrix = np.vstack(vectors) if vectors else np.zeros((0, embedding_dim))
    cols = [f"filing_emb_{i}" for i in range(embedding_dim)]
    emb_df = pd.DataFrame(emb_matrix, columns=cols, index=merged.index)
    return pd.concat([merged[["ticker", "period"]], emb_df], axis=1)
