"""Persisting a trained ModelSet to a directory so a later process (a
different `ml predict` run, possibly on a different day) can load it and
predict without retraining. Needs the optional `ml` dependency group."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import lightgbm as lgb
import pandas as pd

from ml.models import ModelSet, Predictor

_QUANTILE_FILES = {0.1: "q0.1.txt", 0.5: "q0.5.txt", 0.9: "q0.9.txt"}
_MEAN_FILE = "mean.txt"
_META_FILE = "meta.json"
_ROUTES_FILE = "routes.parquet"


def save_model_set(models: ModelSet, directory: Path, *, version: str, routes: pd.DataFrame) -> None:
    """`version` and `routes` (the vocabulary `ml.features.route_ids` built for
    this fit) are published together with the boosters -- `predict` needs the
    same vocabulary `train` used, never a vocabulary rebuilt from its own,
    differently-dated `runs` snapshot (see `route_ids`'s own docstring)."""
    # ModelSet.mean/quantiles are typed as the narrower Predictor protocol
    # (just .predict), but fit() only ever constructs them from lgb.train,
    # so save_model is always actually there -- cast rather than widen
    # ModelSet's own type for one caller.
    directory.mkdir(parents=True, exist_ok=True)
    cast(lgb.Booster, models.mean).save_model(str(directory / _MEAN_FILE))
    for alpha, booster in models.quantiles.items():
        cast(lgb.Booster, booster).save_model(str(directory / _QUANTILE_FILES[alpha]))
    routes.to_parquet(directory / _ROUTES_FILE)
    (directory / _META_FILE).write_text(json.dumps({"interval_shift": models.interval_shift, "version": version}))


def load_model_set(directory: Path) -> ModelSet:
    mean = lgb.Booster(model_file=str(directory / _MEAN_FILE))
    quantiles: dict[float, Predictor] = {
        alpha: lgb.Booster(model_file=str(directory / name)) for alpha, name in _QUANTILE_FILES.items()
    }
    meta = json.loads((directory / _META_FILE).read_text())
    return ModelSet(mean=mean, quantiles=quantiles, interval_shift=meta["interval_shift"])


def load_routes(directory: Path) -> pd.DataFrame:
    return pd.read_parquet(directory / _ROUTES_FILE)
