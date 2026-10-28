"""Forecast models behind one interface: fit on past delivery days, predict the next one.

- Naive benchmarks: the price at the same hour on the forecast date ("yesterday") or a
  week before the delivery day. They need no fitting and are what a model must beat.
- Ridge: a linear model on readable features, with the hour, weekday and month one-hot.
- LightGBM: gradient-boosted trees; point forecast and quantiles for an interval.
"""

from dataclasses import dataclass, field
from typing import Protocol

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from energy_pipeline.forecast.features import TARGET, Variant, design

QUANTILES = (0.1, 0.5, 0.9)
INTERVAL = 0.8  # nominal coverage of the p10-p90 interval
# With relative=True a model predicts the deviation from a recent level, the mean price at
# the same hour over the last week, since trees cannot extrapolate to a price level they
# have not seen. On the validation period predicting the price itself was better for both
# models (`forecast validate`), so that is the default.
REFERENCE = "price_same_hour_mean_7d"
REFERENCE_FALLBACK = "base_mean_7d"


class Model(Protocol):
    name: str

    def fit(self, frame: pd.DataFrame) -> None: ...

    def predict(self, frame: pd.DataFrame) -> pd.DataFrame:
        """One row per row of `frame`: column `prediction`, plus p10/p50/p90 if any."""
        ...


def reference(frame: pd.DataFrame, relative: bool = True) -> pd.Series:
    if not relative:
        return pd.Series(0.0, index=frame.index)
    return frame[REFERENCE].fillna(frame[REFERENCE_FALLBACK])


@dataclass
class SameHourBenchmark:
    """Repeat a known price of the same hour. The spring DST day has no 02:00 to repeat;
    those hours fall back to the base price of the forecast date."""

    name: str
    column: str

    def fit(self, frame: pd.DataFrame) -> None:
        pass

    def predict(self, frame: pd.DataFrame) -> pd.DataFrame:
        prediction = frame[self.column].fillna(frame["base_d"])
        return pd.DataFrame({"prediction": prediction.to_numpy()}, index=frame.index)


def yesterday() -> SameHourBenchmark:
    return SameHourBenchmark("benchmark_yesterday", "price_same_hour_d")


def week_ago() -> SameHourBenchmark:
    return SameHourBenchmark("benchmark_week_ago", "price_same_hour_week_ago")


ONE_HOT = ["local_hour", "day_of_week", "month"]


@dataclass
class RidgeModel:
    variant: Variant
    alpha: float = 1000.0  # chosen on the validation period
    relative: bool = False
    name: str = "ridge"
    _pipeline: object = field(default=None, init=False, repr=False)

    def _inputs(self, frame: pd.DataFrame) -> pd.DataFrame:
        return design(frame, self.variant)

    def fit(self, frame: pd.DataFrame) -> None:
        inputs = self._inputs(frame)
        numeric = [c for c in inputs.columns if c not in ONE_HOT]
        prepare = ColumnTransformer(
            [
                ("one_hot", OneHotEncoder(handle_unknown="ignore"), ONE_HOT),
                (
                    "numeric",
                    make_pipeline(
                        SimpleImputer(strategy="median", add_indicator=True), StandardScaler()
                    ),
                    numeric,
                ),
            ]
        )
        self._pipeline = make_pipeline(prepare, Ridge(alpha=self.alpha))
        self._pipeline.fit(inputs, frame[TARGET] - reference(frame, self.relative))

    def predict(self, frame: pd.DataFrame) -> pd.DataFrame:
        level = reference(frame, self.relative).to_numpy()
        prediction = self._pipeline.predict(self._inputs(frame)) + level
        return pd.DataFrame({"prediction": prediction}, index=frame.index)


LIGHTGBM_PARAMS = {
    "n_estimators": 500,
    "learning_rate": 0.03,
    "num_leaves": 31,
    "min_child_samples": 40,
    "subsample": 0.8,
    "subsample_freq": 1,
    "colsample_bytree": 0.8,
    "n_jobs": 1,  # the backtest fits many days in parallel instead
    "verbose": -1,
}


@dataclass
class LightGBMModel:
    """Point forecast, optionally with a p10-p90 interval from quantile models.

    Quantile models fitted on the training data are overconfident on new days. With
    calibration_days > 0 the interval is conformalized (CQR): the quantile models are fitted
    without the last `calibration_days` delivery days, and p10/p90 are moved apart by the
    margin that makes 80 % of those days' hours fall inside.
    """

    variant: Variant
    objective: str = "l1"  # MAE; chosen on the validation period
    relative: bool = False
    quantiles: bool = False
    calibration_days: int = 28
    params: dict = field(default_factory=lambda: dict(LIGHTGBM_PARAMS))
    seed: int = 0
    name: str = "lightgbm"
    _models: dict = field(default_factory=dict, init=False, repr=False)
    _margin: float = field(default=0.0, init=False, repr=False)

    def _fit_one(self, inputs, target, objective: str, alpha: float | None = None):
        extra = {"alpha": alpha} if alpha is not None else {}
        model = lgb.LGBMRegressor(
            objective=objective, random_state=self.seed, **self.params, **extra
        )
        return model.fit(inputs, target)

    def fit(self, frame: pd.DataFrame) -> None:
        inputs = design(frame, self.variant)
        target = frame[TARGET] - reference(frame, self.relative)
        self._models = {"prediction": self._fit_one(inputs, target, self.objective)}
        self._margin = 0.0
        if not self.quantiles:
            return
        days = sorted(frame["delivery_date"].unique())
        calibration = frame["delivery_date"].isin(days[len(days) - self.calibration_days :])
        if self.calibration_days == 0:
            calibration[:] = False
        proper = ~calibration
        for q in QUANTILES:
            self._models[f"p{round(q * 100)}"] = self._fit_one(
                inputs[proper], target[proper], "quantile", q
            )
        if calibration.any():
            held_out = self._quantiles(frame[calibration])
            actual = frame.loc[calibration, TARGET].to_numpy()
            scores = np.maximum(held_out["p10"] - actual, actual - held_out["p90"])
            level = min(1.0, np.ceil((len(scores) + 1) * INTERVAL) / len(scores))
            self._margin = float(np.quantile(scores, level, method="higher"))

    def _quantiles(self, frame: pd.DataFrame) -> pd.DataFrame:
        inputs = design(frame, self.variant)
        level = reference(frame, self.relative).to_numpy()
        columns = [f"p{round(q * 100)}" for q in QUANTILES]
        # Separately fitted quantiles can cross; sorting restores p10 <= p50 <= p90.
        values = np.sort(
            np.column_stack([self._models[c].predict(inputs) + level for c in columns]), axis=1
        )
        return pd.DataFrame(values, columns=columns, index=frame.index)

    def predict(self, frame: pd.DataFrame) -> pd.DataFrame:
        inputs = design(frame, self.variant)
        level = reference(frame, self.relative).to_numpy()
        result = pd.DataFrame(
            {"prediction": self._models["prediction"].predict(inputs) + level}, index=frame.index
        )
        if self.quantiles:
            interval = self._quantiles(frame)
            # A negative margin narrows the interval, but never across the median.
            interval["p10"] = np.minimum(interval["p10"] - self._margin, interval["p50"])
            interval["p90"] = np.maximum(interval["p90"] + self._margin, interval["p50"])
            result = result.join(interval)
        return result

    def gain(self) -> pd.Series:
        """Total split gain per feature of the point model, as a share of all gain."""
        booster = self._models["prediction"].booster_
        gain = pd.Series(
            booster.feature_importance(importance_type="gain"), index=booster.feature_name()
        )
        return (gain / gain.sum()).rename("gain_share")

    def contributions(self, frame: pd.DataFrame) -> pd.DataFrame:
        """SHAP values of the point model (TreeSHAP built into LightGBM), one column per
        feature plus `expected_value`, in EUR/MWh of deviation from the reference."""
        inputs = design(frame, self.variant)
        values = self._models["prediction"].predict(inputs, pred_contrib=True)
        return pd.DataFrame(values, columns=[*inputs.columns, "expected_value"], index=frame.index)
