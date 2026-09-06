import numpy as np
import pandas as pd
import pytest

from stock_valuation.data.filings import extract_section, select_point_in_time_filing
from stock_valuation.embeddings import embed_texts, fit_reducer, reduce_vectors
from stock_valuation.text_features import attach_text_embeddings, build_filing_lookup


def test_extract_section_pulls_risk_factors_between_item_headings():
    text = (
        "PART I Item 1. Business blah blah "
        "Item 1A. Risk Factors Our supply chain is concentrated and a single "
        "disruption could hurt margins significantly. "
        "Item 1B. Unresolved Staff Comments none."
    )
    section = extract_section(text, "risk_factors")
    assert "supply chain" in section.lower()
    assert "unresolved staff comments" not in section.lower()


def test_extract_section_returns_empty_string_when_heading_missing():
    assert extract_section("no item headings here at all", "risk_factors") == ""


def test_select_point_in_time_filing_never_returns_a_future_filing():
    history = pd.DataFrame(
        {
            "form": ["10-K", "10-Q", "10-Q"],
            "filing_date": pd.to_datetime(["2022-02-01", "2022-05-01", "2022-08-01"]),
            "accession_number": ["a1", "a2", "a3"],
            "primary_document": ["d1.htm", "d2.htm", "d3.htm"],
        }
    )
    # as_of sits between the 2nd and 3rd filing — must pick the 2nd, never the 3rd.
    picked = select_point_in_time_filing(history, pd.Timestamp("2022-06-30"))
    assert picked["accession_number"] == "a2"


def test_select_point_in_time_filing_returns_none_before_any_filing():
    history = pd.DataFrame(
        {
            "form": ["10-K"],
            "filing_date": pd.to_datetime(["2022-02-01"]),
            "accession_number": ["a1"],
            "primary_document": ["d1.htm"],
        }
    )
    assert select_point_in_time_filing(history, pd.Timestamp("2021-01-01")) is None


def test_build_filing_lookup_matches_each_period_to_its_point_in_time_filing():
    periods = pd.DataFrame(
        {
            "ticker": ["AAA", "AAA", "BBB"],
            "period": pd.to_datetime(["2022-03-31", "2022-09-30", "2022-03-31"]),
        }
    )
    histories = {
        "AAA": pd.DataFrame(
            {
                "form": ["10-K", "10-Q"],
                "filing_date": pd.to_datetime(["2022-01-15", "2022-07-15"]),
                "accession_number": ["a1", "a2"],
                "primary_document": ["d1.htm", "d2.htm"],
            }
        ),
        # BBB has no filing history yet — should come back with no match.
    }
    lookup = build_filing_lookup(periods, histories)
    assert lookup.loc[0, "accession_number"] == "a1"
    assert lookup.loc[1, "accession_number"] == "a2"
    assert pd.isna(lookup.loc[2, "accession_number"])


def test_attach_text_embeddings_caches_by_unique_filing():
    periods = pd.DataFrame(
        {"ticker": ["AAA", "AAA", "BBB"], "period": pd.to_datetime(["2022-03-31", "2022-06-30", "2022-03-31"])}
    )
    lookup = pd.DataFrame(
        {
            "ticker": ["AAA", "AAA", "BBB"],
            "period": pd.to_datetime(["2022-03-31", "2022-06-30", "2022-03-31"]),
            # AAA's two quarters share the same filing; BBB has none yet.
            "accession_number": ["a1", "a1", None],
            "primary_document": ["d1.htm", "d1.htm", None],
        }
    )
    call_count = {"n": 0}

    def fake_fetch_and_embed(ticker, accession_number, primary_document):
        call_count["n"] += 1
        return np.array([1.0, 2.0])

    result = attach_text_embeddings(periods, lookup, fake_fetch_and_embed, embedding_dim=2)
    assert call_count["n"] == 1  # fetched AAA's shared filing exactly once
    assert list(result.columns) == ["ticker", "period", "filing_emb_0", "filing_emb_1"]
    assert result.loc[result["ticker"] == "BBB", "filing_emb_0"].iloc[0] == 0.0


def test_embed_texts_zero_vectors_for_empty_strings():
    class FakeModel:
        def encode(self, texts, show_progress_bar=False):
            return np.array([[float(len(t))] * 3 for t in texts])

    vectors = embed_texts(["hello", "", "hi"], model=FakeModel())
    assert vectors[1].tolist() == [0.0, 0.0, 0.0]
    assert vectors[0].tolist() != [0.0, 0.0, 0.0]


def test_fit_reducer_and_reduce_vectors_roundtrip_shape():
    rng = np.random.default_rng(0)
    vectors = rng.normal(size=(20, 10))
    pca = fit_reducer(vectors, n_components=4)
    reduced = reduce_vectors(pca, vectors)
    assert reduced.shape == (20, 4)
