"""The R2 horizons the methodology and the retention table publish are the
ones prune-r2.sh enforces by default."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _default(var: str) -> int:
    script = (ROOT / "oracle_cloud/v3/bin/prune-r2.sh").read_text(encoding="utf-8")
    match = re.search(rf'^{var}="\$\{{{var}-(\d+)\}}"$', script, re.MULTILINE)
    assert match, f"prune-r2.sh no longer sets a default for {var}"
    return int(match[1])


def _line(path: str, needle: str) -> str:
    lines = [line for line in (ROOT / path).read_text(encoding="utf-8").splitlines() if needle in line]
    assert len(lines) == 1, f"{path}: expected one line naming {needle!r}, found {len(lines)}"
    return lines[0]


def test_the_methodology_states_prune_r2s_vp_and_rt_horizons():
    vp, rt = _default("R2_VP_RETENTION_DAYS"), _default("R2_RT_RETENTION_DAYS")
    en = _line("docs/methodology.en.md", "Positions are kept only to validate")
    assert f"kept for {vp} days" in en
    assert f"realtime archive's {rt:,}" in en
    ja = _line("docs/methodology.md", "位置情報は、予測値の検証のためだけに")
    assert f"{vp}日間" in ja
    assert f"（{rt:,}日）" in ja


def test_the_retention_table_states_prune_r2s_horizons():
    doc = "docs/ops-monitoring-deploy.md"
    assert f"| {_default('R2_RT_RETENTION_DAYS'):,} days |" in _line(doc, "| R2 `rt/<id>/`")
    assert f"| {_default('R2_STATIC_RETENTION_DAYS'):,} days;" in _line(doc, "| R2 `static/<id>/`")
    assert f"| {_default('R2_VP_RETENTION_DAYS'):,} days |" in _line(doc, "| R2 `vp/<id>/`")
