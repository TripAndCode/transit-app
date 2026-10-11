"""Persisting a trained ModelSet to a directory so a later process (a
different `ml predict` run, possibly on a different day) can load it and
predict without retraining. Needs the optional `ml` dependency group."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import lightgbm as lgb

from ml.models import ModelSet, Predictor

_QUANTILE_FILES = {0.1: "q0.1.txt", 0.5: "q0.5.txt", 0.9: "q0.9.txt"}
_MEAN_FILE = "mean.txt"
_META_FILE = "meta.json"


def save_model_set(models: ModelSet, directory: Path) -> None:
    # ModelSet.mean/quantiles are typed as the narrower Predictor protocol
    # (just .predict), but fit() only ever constructs them from lgb.train,
    # so save_model is always actually there -- cast rather than widen
    # ModelSet's own type for one caller.
    directory.mkdir(parents=True, exist_ok=True)
    cast(lgb.Booster, models.mean).save_model(str(directory / _MEAN_FILE))
    for alpha, booster in models.quantiles.items():
        cast(lgb.Booster, booster).save_model(str(directory / _QUANTILE_FILES[alpha]))
    (directory / _META_FILE).write_text(json.dumps({"interval_shift": models.interval_shift}))


def load_model_set(directory: Path) -> ModelSet:
    mean = lgb.Booster(model_file=str(directory / _MEAN_FILE))
    quantiles: dict[float, Predictor] = {
        alpha: lgb.Booster(model_file=str(directory / name)) for alpha, name in _QUANTILE_FILES.items()
    }
    meta = json.loads((directory / _META_FILE).read_text())
    return ModelSet(mean=mean, quantiles=quantiles, interval_shift=meta["interval_shift"])
