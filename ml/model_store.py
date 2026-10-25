"""Persisting a trained ModelSet to a directory so a later process (a
different `ml predict` run, possibly on a different day) can load it and
predict without retraining. Needs the optional `ml` dependency group."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import cast

import lightgbm as lgb
import pandas as pd

from ml.models import ModelSet, Predictor

_QUANTILE_FILES = {0.1: "q0.1.txt", 0.5: "q0.5.txt", 0.9: "q0.9.txt"}
_MEAN_FILE = "mean.txt"
_META_FILE = "meta.json"
_ROUTES_FILE = "routes.parquet"


def stage_model_set(
    models: ModelSet,
    directory: Path,
    *,
    version: str,
    routes: pd.DataFrame,
    window_days: int,
    point: str,
    clip_quantile: float | None,
) -> Path:
    """Writes the model set into a sibling temp directory of `directory` and
    returns it, leaving `directory` itself untouched until `replace_dir` moves
    the staged set into place. A failure partway through never leaves
    `directory` holding a mix of a new and a previous fit's files, and the
    caller can publish the staged set elsewhere before it replaces anything.

    `version`, `window_days` (the training window the published model was
    actually fit with), `clip_quantile` (the history clip its features were
    built with), `point` (the forecast it was adopted to show) and `routes` (the
    vocabulary `ml.features.route_ids` built for this fit) are published
    together with the boosters -- `predict` needs the same vocabulary, window
    and features `train` used, never values rebuilt or re-guessed from its own,
    differently-dated `runs` snapshot or its own CLI default (see `route_ids`'s
    own docstring on the vocabulary hazard)."""
    if point == "mean" and models.mean is None:
        raise ValueError("a model set without its mean model cannot be published to show the mean")
    directory.parent.mkdir(parents=True, exist_ok=True)
    tmp = directory.with_name(directory.name + ".tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    # ModelSet.mean/quantiles are typed as the narrower Predictor protocol
    # (just .predict), but fit() only ever constructs them from lgb.train,
    # so save_model is always actually there -- cast rather than widen
    # ModelSet's own type for one caller.
    if models.mean is not None:
        cast(lgb.Booster, models.mean).save_model(str(tmp / _MEAN_FILE))
    for alpha, booster in models.quantiles.items():
        cast(lgb.Booster, booster).save_model(str(tmp / _QUANTILE_FILES[alpha]))
    routes.to_parquet(tmp / _ROUTES_FILE)
    meta = {
        "interval_shift": models.interval_shift,
        "version": version,
        "window_days": window_days,
        "point": point,
        "clip_quantile": clip_quantile,
    }
    (tmp / _META_FILE).write_text(json.dumps(meta))
    return tmp


def replace_dir(staged: Path, directory: Path) -> None:
    """Moves `staged` to `directory`, replacing what was there. Both must be
    on the same filesystem, as `stage_model_set`'s sibling is."""
    if directory.exists():
        shutil.rmtree(directory)
    staged.rename(directory)


def load_model_set(directory: Path) -> ModelSet:
    """A model set published without its mean model loads with `mean` None."""
    mean_file = directory / _MEAN_FILE
    mean = lgb.Booster(model_file=str(mean_file)) if mean_file.exists() else None
    quantiles: dict[float, Predictor] = {
        alpha: lgb.Booster(model_file=str(directory / name)) for alpha, name in _QUANTILE_FILES.items()
    }
    meta = read_meta(directory)
    return ModelSet(mean=mean, quantiles=quantiles, interval_shift=meta["interval_shift"])


def load_routes(directory: Path) -> pd.DataFrame:
    return pd.read_parquet(directory / _ROUTES_FILE)


def read_meta(directory: Path) -> dict:
    return json.loads((directory / _META_FILE).read_text())
