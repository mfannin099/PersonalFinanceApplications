"""A deliberately small XGBoost model for the forward return of a stock — pure, Shiny-free.

Design choices, in order of importance:

* **Predict the return, not the price.** A price level is non-stationary and trees can't
  extrapolate beyond values seen in training, so a price model just repeats the recent
  past. Returns are roughly stationary, which is what a tree model needs.
* **Time-based features only** (all computed from data up to day *t*): lagged returns,
  momentum, volatility, distance from moving averages, and calendar effects.
* **Honest validation.** Walk-forward (expanding window) folds, with a gap of ``horizon``
  days between train and test so overlapping targets can't leak, scored against naive
  baselines. Random shuffling would leak the future and flatter the model.
* **Fixed, conservative hyper-parameters** — shallow trees, small learning rate,
  subsampling, strong regularisation. No search, so there's nothing to overfit to.

Daily stock returns are very close to noise, so expect modest (often zero) skill. The
walk-forward report exists precisely to show whether the model beats guessing.
"""

import numpy as np
import pandas as pd
from xgboost import XGBRegressor

PARAMS = {
    "n_estimators": 200,
    "max_depth": 2,  # shallow: each tree can combine at most two features
    "learning_rate": 0.03,
    "subsample": 0.7,
    "colsample_bytree": 0.7,
    "min_child_weight": 10,  # a leaf needs real support, not a handful of days
    "reg_lambda": 5.0,
    "objective": "reg:squarederror",
    "tree_method": "hist",
    "random_state": 42,
    "n_jobs": 1,
}
FOLDS = 4
MIN_TRAIN_ROWS = 150
FEATURE_WARMUP_DAYS = 63  # longest feature lookback
BAND_QUANTILES = (0.10, 0.90)

FEATURE_GROUPS = {
    "Recent returns": ["ret_lag1", "ret_lag2", "ret_lag3", "ret_lag5"],
    "Momentum": ["mom_5", "mom_21", "mom_63"],
    "Volatility": ["vol_10", "vol_21"],
    "Trend (vs. moving average)": ["dist_sma20", "dist_sma50"],
    "Calendar": ["day_of_week", "month"],
}
FEATURES = [name for names in FEATURE_GROUPS.values() for name in names]


def build_features(close: pd.Series) -> pd.DataFrame:
    """Features for every day, using only information available at that day's close."""
    close = close.dropna()
    log_ret = np.log(close).diff()
    feats = pd.DataFrame(index=close.index)
    for lag in (1, 2, 3, 5):
        feats[f"ret_lag{lag}"] = log_ret.shift(lag - 1)  # lag 1 = today's own return
    for window in (5, 21, 63):
        feats[f"mom_{window}"] = np.log(close / close.shift(window))
    for window in (10, 21):
        feats[f"vol_{window}"] = log_ret.rolling(window).std()
    for window in (20, 50):
        feats[f"dist_sma{window}"] = close / close.rolling(window).mean() - 1
    feats["day_of_week"] = close.index.dayofweek
    feats["month"] = close.index.month
    return feats[FEATURES]


def forward_return(close: pd.Series, horizon: int) -> pd.Series:
    """Log return from each day's close to the close ``horizon`` trading days later (NaN at the end)."""
    close = close.dropna()
    return np.log(close.shift(-horizon) / close)


def _fit(x: pd.DataFrame, y: pd.Series) -> XGBRegressor:
    return XGBRegressor(**PARAMS).fit(x, y)


def walk_forward(x: pd.DataFrame, y: pd.Series, horizon: int, folds: int = FOLDS) -> pd.DataFrame:
    """Out-of-sample predictions from an expanding-window walk-forward.

    The last 40% of rows is split into ``folds`` consecutive test blocks. Each block is
    predicted by a model trained only on rows at least ``horizon`` days before it (the
    purge gap), so no training target overlaps the period being tested.

    Returns ``Predicted``, ``Actual`` and ``Baseline`` (the training-window mean return,
    the "no skill" guess) indexed by date.
    """
    n = len(x)
    first_test = int(n * 0.6)
    edges = np.linspace(first_test, n, folds + 1, dtype=int)
    parts = []
    for start, end in zip(edges[:-1], edges[1:], strict=True):
        train_end = start - horizon
        if train_end < MIN_TRAIN_ROWS or end <= start:
            continue
        model = _fit(x.iloc[:train_end], y.iloc[:train_end])
        parts.append(
            pd.DataFrame(
                {
                    "Predicted": model.predict(x.iloc[start:end]),
                    "Actual": y.iloc[start:end].to_numpy(),
                    "Baseline": float(y.iloc[:train_end].mean()),
                },
                index=x.index[start:end],
            )
        )
    if not parts:
        raise ValueError("Not enough history to validate the model — choose a longer lookback.")
    return pd.concat(parts)


def score(oos: pd.DataFrame) -> dict:
    """Out-of-sample scorecard: the model against two naive guesses."""
    err_model = (oos["Actual"] - oos["Predicted"]).abs().mean()
    err_base = (oos["Actual"] - oos["Baseline"]).abs().mean()
    up_rate = float((oos["Actual"] > 0).mean())
    hit_rate = float((np.sign(oos["Predicted"]) == np.sign(oos["Actual"])).mean())
    corr = oos["Predicted"].corr(oos["Actual"])
    return {
        "mae_model": float(err_model),
        "mae_baseline": float(err_base),
        "skill": float(1 - err_model / err_base) if err_base else float("nan"),  # >0 means beats the baseline
        "hit_rate": hit_rate,
        "up_rate": up_rate,  # hit rate of always guessing "up" would be this
        "correlation": float(corr) if pd.notna(corr) else float("nan"),
        "test_days": int(len(oos)),
    }


def verdict(metrics: dict) -> str:
    """One plain-English line summarising whether the model showed real out-of-sample skill."""
    skill, corr = metrics["skill"], metrics["correlation"]
    beats_up = metrics["hit_rate"] > max(metrics["up_rate"], 1 - metrics["up_rate"]) + 0.02
    if skill > 0.02 and corr > 0.1 and beats_up:
        return "The model beat the naive baselines out-of-sample — a modest edge, but treat it with caution."
    if skill > 0 and corr > 0:
        return "The model was marginally better than guessing out-of-sample — too small to rely on."
    return "The model did not beat simple baselines out-of-sample — its prediction is not better than guessing."


def train_and_forecast(close: pd.Series, horizon: int) -> dict:
    """Validate, then fit on all data and project ``horizon`` trading days ahead.

    Raises ``ValueError`` if there isn't enough history. The result holds the predicted
    return/price and an empirical 80% band (from the out-of-sample errors, so it reflects
    how wrong the model actually was), the walk-forward scorecard and predictions, and
    gain-based feature importance.
    """
    close = close.dropna()
    if horizon < 1:
        raise ValueError("Forecast horizon must be at least 1 day.")
    feats = build_features(close).dropna()
    target = forward_return(close, horizon)
    labelled = feats.join(target.rename("target")).dropna()
    if int(len(labelled) * 0.6) - horizon < MIN_TRAIN_ROWS:  # the first fold must still have enough to train on
        raise ValueError(
            f"Only {len(labelled)} usable days after feature warm-up and a {horizon}-day horizon — "
            "choose a longer lookback (2+ years works best) or a shorter horizon."
        )
    x, y = labelled[FEATURES], labelled["target"]

    oos = walk_forward(x, y, horizon)
    metrics = score(oos)

    model = _fit(x, y)  # final model sees everything that has a known outcome
    latest = feats.iloc[[-1]]
    predicted_return = float(model.predict(latest)[0])
    last_close = float(close.iloc[-1])
    lo_q, hi_q = np.quantile(oos["Actual"] - oos["Predicted"], BAND_QUANTILES)

    gain = pd.Series(model.get_booster().get_score(importance_type="gain"), dtype=float).reindex(FEATURES).fillna(0)
    importance = gain / gain.sum() if gain.sum() > 0 else gain
    return {
        "horizon": horizon,
        "as_of": close.index[-1],
        "last_close": last_close,
        "predicted_return": predicted_return,
        "predicted_price": last_close * float(np.exp(predicted_return)),
        "lower_price": last_close * float(np.exp(predicted_return + lo_q)),
        "upper_price": last_close * float(np.exp(predicted_return + hi_q)),
        "target_date": close.index[-1] + pd.offsets.BDay(horizon),
        "metrics": metrics,
        "verdict": verdict(metrics),
        "oos": oos,
        "importance": importance.sort_values(ascending=False),
        "n_rows": int(len(labelled)),
    }
