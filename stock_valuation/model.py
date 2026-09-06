from __future__ import annotations

import lightgbm as lgb
import pandas as pd
from sklearn.metrics import accuracy_score, classification_report


def time_split(df: pd.DataFrame, period_col: str = "period", test_frac: float = 0.2):
    """Split by period, not row, so no ticker's future period leaks into the
    training set for another ticker's earlier one."""
    periods = sorted(df[period_col].unique())
    cutoff = periods[int(len(periods) * (1 - test_frac))]
    train = df[df[period_col] < cutoff]
    test = df[df[period_col] >= cutoff]
    return train, test


def train_quality_model(
    df: pd.DataFrame, feature_cols: list[str], label_col: str = "label"
) -> tuple[lgb.LGBMClassifier, dict]:
    """Train a LightGBM classifier predicting long-term relative-return tier
    from fundamentals + macro features only (no price, no analyst data)."""
    train, test = time_split(df)
    train = train.dropna(subset=feature_cols + [label_col])
    test = test.dropna(subset=feature_cols + [label_col])

    model = lgb.LGBMClassifier(
        n_estimators=300,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        random_state=42,
        verbosity=-1,
    )
    model.fit(train[feature_cols], train[label_col])

    metrics = {"n_train": len(train), "n_test": len(test)}
    if len(test) > 0:
        preds = model.predict(test[feature_cols])
        metrics["accuracy"] = accuracy_score(test[label_col], preds)
        metrics["report"] = classification_report(test[label_col], preds, zero_division=0)

    return model, metrics


def predict_quality_score(
    model: lgb.LGBMClassifier, df: pd.DataFrame, feature_cols: list[str]
) -> pd.Series:
    """Probability of landing in the top return tier — the "quality score"
    used downstream to gate the valuation-gap buy signal."""
    top_tier = model.classes_.max()
    proba = model.predict_proba(df[feature_cols])
    top_idx = list(model.classes_).index(top_tier)
    return pd.Series(proba[:, top_idx], index=df.index, name="quality_score")
