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

from Stocks.forecast import MA_KINDS, moving_average

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
INNER_FOLDS = 3
INNER_MIN_TRAIN_ROWS = 80
# Bounds for user-adjustable hyper-parameters (the app's sliders use the same ranges).
PARAM_BOUNDS = {
    "max_depth": (1, 4),
    "n_estimators": (50, 500),
    "learning_rate": (0.01, 0.2),
    "min_child_weight": (1, 50),
    "subsample": (0.5, 1.0),
    "colsample_bytree": (0.5, 1.0),
    "reg_lambda": (0.0, 20.0),
}
# The only things auto-tune searches: tree depth and learning rate. Small on purpose —
# every extra option searched is another chance to pick one that fits past noise by luck.
TUNE_GRID = [{"max_depth": d, "learning_rate": lr} for d in (1, 2, 3) for lr in (0.02, 0.05)]
FEATURE_WARMUP_DAYS = 63  # longest fixed feature lookback (a slower moving average can lengthen it)
DEFAULT_MA = {"kind": "SMA", "fast": 20, "slow": 50}  # the trend features' moving averages (customizable)
MA_BOUNDS = (2, 200)
BAND_QUANTILES = (0.10, 0.90)

FEATURE_GROUPS = {
    "Recent returns": ["ret_lag1", "ret_lag2", "ret_lag3", "ret_lag5"],
    "Momentum": ["mom_5", "mom_21", "mom_63"],
    "Volatility": ["vol_10", "vol_21"],
    "Trend (vs. moving average)": ["dist_ma_fast", "dist_ma_slow"],
    "Calendar": ["day_of_week", "month"],
}
FEATURES = [name for names in FEATURE_GROUPS.values() for name in names]

# Plain-language description of every feature: (readable label, what it measures, how the model may use it).
FEATURE_INFO = {
    "ret_lag1": ("Today's return", "The stock's log return on the latest day.", "Tests whether a big move today tends to continue (momentum) or reverse (mean reversion)."),
    "ret_lag2": ("Return 1 day ago", "The log return the day before.", "Same idea, one day back — helps separate a one-day spike from a sustained move."),
    "ret_lag3": ("Return 2 days ago", "The log return two days before.", "Short-term memory of the last few sessions."),
    "ret_lag5": ("Return 4 days ago", "The log return four days before.", "Reaches back to roughly the start of the trading week."),
    "mom_5": ("5-day momentum", "Price change over the last 5 trading days.", "A week-long trend; strongly positive may signal continuation or an overextended bounce."),
    "mom_21": ("1-month momentum", "Price change over the last 21 trading days (~1 month).", "The classic medium-term trend signal."),
    "mom_63": ("3-month momentum", "Price change over the last 63 trading days (~1 quarter).", "A slower trend; captures the direction the stock has been heading for a quarter."),
    "vol_10": ("10-day volatility", "Standard deviation of daily returns over 10 days.", "Recent turbulence. Calm and stormy periods behave differently, so trees can split on it."),
    "vol_21": ("1-month volatility", "Standard deviation of daily returns over 21 days.", "A steadier read on how jumpy the stock has been."),
    "dist_ma_fast": ("Distance from fast average", "How far today's price sits above (+) or below (−) the fast moving average, as a %.", "Stretched far above its average can mean overheated; far below, oversold — or a real breakdown."),
    "dist_ma_slow": ("Distance from slow average", "How far today's price sits above (+) or below (−) the slow moving average, as a %.", "Shows where price is relative to the long-run trend."),
    "day_of_week": ("Day of week", "Monday=0 … Friday=4 for the latest day.", "Lets the model look for weekday patterns — usually weak or spurious."),
    "month": ("Month of year", "1–12 for the latest day.", "Lets the model look for seasonality — usually weak with only a few years of data."),
}
FEATURE_LABELS = {k: v[0] for k, v in FEATURE_INFO.items()}


def ma_feature_labels(ma: dict | None = None) -> dict[str, str]:
    """``FEATURE_LABELS`` with the two trend features named after the chosen moving averages."""
    ma = clean_ma(ma)
    labels = dict(FEATURE_LABELS)
    labels["dist_ma_fast"] = f"Distance from {ma['fast']}-day {ma['kind']}"
    labels["dist_ma_slow"] = f"Distance from {ma['slow']}-day {ma['kind']}"
    return labels


def clean_ma(ma: dict | None) -> dict:
    """Validated moving-average settings for the trend features (``kind``, ``fast``, ``slow``).

    Raises ``ValueError`` for an unknown kind, windows outside ``MA_BOUNDS``, or fast >= slow.
    """
    out = {**DEFAULT_MA, **{k: v for k, v in (ma or {}).items() if v is not None}}
    if out["kind"] not in MA_KINDS:
        raise ValueError(f"Moving-average type must be one of {', '.join(MA_KINDS)}.")
    out["fast"], out["slow"] = int(out["fast"]), int(out["slow"])
    low, high = MA_BOUNDS
    if not (low <= out["fast"] < out["slow"] <= high):
        raise ValueError(f"Moving-average windows must satisfy {low} ≤ fast < slow ≤ {high} days.")
    return out


def build_features(close: pd.Series, ma: dict | None = None) -> pd.DataFrame:
    """Features for every day, using only information available at that day's close.

    ``ma`` customizes the trend features: ``{"kind": "SMA" | "EMA", "fast": 20, "slow": 50}``.
    """
    ma = clean_ma(ma)
    close = close.dropna()
    log_ret = np.log(close).diff()
    feats = pd.DataFrame(index=close.index)
    for lag in (1, 2, 3, 5):
        feats[f"ret_lag{lag}"] = log_ret.shift(lag - 1)  # lag 1 = today's own return
    for window in (5, 21, 63):
        feats[f"mom_{window}"] = np.log(close / close.shift(window))
    for window in (10, 21):
        feats[f"vol_{window}"] = log_ret.rolling(window).std()
    for name, window in (("fast", ma["fast"]), ("slow", ma["slow"])):
        feats[f"dist_ma_{name}"] = close / moving_average(close, window, ma["kind"]) - 1
    feats["day_of_week"] = close.index.dayofweek
    feats["month"] = close.index.month
    return feats[FEATURES]


def forward_return(close: pd.Series, horizon: int) -> pd.Series:
    """Log return from each day's close to the close ``horizon`` trading days later (NaN at the end)."""
    close = close.dropna()
    return np.log(close.shift(-horizon) / close)


def clean_params(overrides: dict | None) -> dict:
    """``PARAMS`` with user overrides applied, clamped to ``PARAM_BOUNDS``; unknown keys are ignored."""
    params = dict(PARAMS)
    for key, value in (overrides or {}).items():
        if key in PARAM_BOUNDS and value is not None:
            low, high = PARAM_BOUNDS[key]
            clamped = min(max(value, low), high)
            params[key] = int(round(clamped)) if key in ("max_depth", "n_estimators", "min_child_weight") else float(clamped)
    return params


def _fit(x: pd.DataFrame, y: pd.Series, params: dict | None = None) -> XGBRegressor:
    return XGBRegressor(**(params or PARAMS)).fit(x, y)


def tune(x: pd.DataFrame, y: pd.Series, horizon: int, base: dict) -> dict:
    """Pick the best ``TUNE_GRID`` entry by inner walk-forward error, using only ``x``/``y``.

    Each candidate is scored on ``INNER_FOLDS`` blocks that are never in its training data.
    Falls back to ``base`` unchanged when there's too little history to score candidates.
    """
    best, best_err = base, float("inf")
    for candidate in TUNE_GRID:
        trial = {**base, **candidate}
        try:
            oos = walk_forward(x, y, horizon, trial, folds=INNER_FOLDS, start_frac=0.5, min_train=INNER_MIN_TRAIN_ROWS)
        except ValueError:
            return base
        err = float((oos["Actual"] - oos["Predicted"]).abs().mean())
        if err < best_err:
            best, best_err = trial, err
    return best


def walk_forward(
    x: pd.DataFrame,
    y: pd.Series,
    horizon: int,
    params: dict | None = None,
    folds: int = FOLDS,
    start_frac: float = 0.6,
    min_train: int = MIN_TRAIN_ROWS,
    auto_tune: bool = False,
) -> pd.DataFrame:
    """Out-of-sample predictions from an expanding-window walk-forward.

    Rows after ``start_frac`` are split into ``folds`` consecutive test blocks. Each block is
    predicted by a model trained only on rows at least ``horizon`` days before it (the
    purge gap), so no training target overlaps the period being tested. With ``auto_tune``,
    hyper-parameters are chosen *inside each training window* (nested), never using the block
    being tested — so tuning can't flatter the score.

    Returns ``Predicted``, ``Actual`` and ``Baseline`` (the training-window mean return,
    the "no skill" guess) indexed by date.
    """
    params = params or PARAMS
    n = len(x)
    edges = np.linspace(int(n * start_frac), n, folds + 1, dtype=int)
    parts = []
    for start, end in zip(edges[:-1], edges[1:], strict=True):
        train_end = start - horizon
        if train_end < min_train or end <= start:
            continue
        x_train, y_train = x.iloc[:train_end], y.iloc[:train_end]
        fold_params = tune(x_train, y_train, horizon, params) if auto_tune else params
        model = _fit(x_train, y_train, fold_params)
        parts.append(
            pd.DataFrame(
                {
                    "Predicted": model.predict(x.iloc[start:end]),
                    "Actual": y.iloc[start:end].to_numpy(),
                    "Baseline": float(y_train.mean()),
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


def train_and_forecast(
    close: pd.Series,
    horizon: int,
    params: dict | None = None,
    features: list[str] | None = None,
    auto_tune: bool = False,
    ma: dict | None = None,
) -> dict:
    """Validate, then fit on all data and project ``horizon`` trading days ahead.

    ``params`` overrides the defaults (clamped to ``PARAM_BOUNDS``), ``features`` limits the
    model to a subset of ``FEATURES``, and ``auto_tune`` searches ``TUNE_GRID`` (nested, so the
    scorecard stays honest). ``ma`` sets the trend features' moving averages (see ``build_features``). Raises ``ValueError`` on too little history or no features.

    The result holds the predicted return/price and an empirical 80% band (from the
    out-of-sample errors, so it reflects how wrong the model actually was), the walk-forward
    scorecard and predictions, gain-based feature importance, the settings used, and the
    in-sample error — compare it with the out-of-sample error: a big gap means overfitting.
    """
    close = close.dropna()
    if horizon < 1:
        raise ValueError("Forecast horizon must be at least 1 day.")
    features = [f for f in FEATURES if features is None or f in features]
    if not features:
        raise ValueError("Select at least one feature.")
    base = clean_params(params)
    ma = clean_ma(ma)

    feats = build_features(close, ma).dropna()
    target = forward_return(close, horizon)
    labelled = feats.join(target.rename("target")).dropna()
    if int(len(labelled) * 0.6) - horizon < MIN_TRAIN_ROWS:  # the first fold must still have enough to train on
        raise ValueError(
            f"Only {len(labelled)} usable days after feature warm-up and a {horizon}-day horizon — "
            "choose a longer lookback (2+ years works best) or a shorter horizon."
        )
    x, y = labelled[features], labelled["target"]

    oos = walk_forward(x, y, horizon, base, auto_tune=auto_tune)
    metrics = score(oos)

    final_params = tune(x, y, horizon, base) if auto_tune else base
    model = _fit(x, y, final_params)  # final model sees everything that has a known outcome
    latest = feats[features].iloc[[-1]]
    predicted_return = float(model.predict(latest)[0])
    last_close = float(close.iloc[-1])
    lo_q, hi_q = np.quantile(oos["Actual"] - oos["Predicted"], BAND_QUANTILES)

    gain = pd.Series(model.get_booster().get_score(importance_type="gain"), dtype=float).reindex(features).fillna(0)
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
        "train_mae": float((y - model.predict(x)).abs().mean()),
        "oos": oos,
        "importance": importance.sort_values(ascending=False),
        "n_rows": int(len(labelled)),
        "features": features,
        "params": {k: final_params[k] for k in PARAM_BOUNDS},
        "auto_tuned": auto_tune,
        "ma": ma,
    }
