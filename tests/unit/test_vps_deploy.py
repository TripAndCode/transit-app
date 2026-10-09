"""Invariants of the VPS replica stack. The replica holds a copy of production
history, so its listeners stay on loopback, its memory stays inside the box,
and nothing it owns can be mistaken for the dev stores the guard hook protects."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
VPS = ROOT / "deploy" / "vps"
DEV_VOLUMES = ("transit_pgdata", "transit_chdata")
DEV_PORTS = ("5433", "5543", "8123")
# Every system log ClickHouse 26.8 writes on its own. The others (backup_log,
# s3queue_log, query_views_log, …) fill only from features the replica never uses.
SYSTEM_LOGS = (
    "query_log",
    "query_metric_log",
    "trace_log",
    "text_log",
    "error_log",
    "metric_log",
    "asynchronous_metric_log",
    "part_log",
    "processors_profile_log",
    "background_schedule_pool_log",
)


def _compose() -> dict:
    return yaml.safe_load((VPS / "compose.yml").read_text())


def test_every_published_port_binds_loopback_and_avoids_dev_ports():
    for name, service in _compose()["services"].items():
        for port in service.get("ports", []):
            host, published, _container = str(port).split(":")
            assert host == "127.0.0.1", f"{name} publishes {port} beyond loopback"
            assert published not in DEV_PORTS, f"{name} takes dev port {published}"


def test_both_services_carry_a_memory_limit():
    services = _compose()["services"]
    assert services["clickhouse"]["mem_limit"] == "1536m"
    assert services["postgres"]["mem_limit"] == "640m"


def test_volumes_cannot_be_mistaken_for_dev_stores():
    names = set(_compose()["volumes"])
    assert names == {"ml_pg", "ml_ch"}
    assert not any(dev in name for dev in DEV_VOLUMES for name in names)


def test_clickhouse_image_matches_ci():
    ci = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    ci_image = ci["jobs"]["test"]["services"]["clickhouse"]["image"]
    assert _compose()["services"]["clickhouse"]["image"] == ci_image


def test_clickhouse_server_memory_is_capped_below_the_container_limit():
    root = ET.parse(VPS / "clickhouse" / "config.d" / "memory.xml").getroot()
    cap = int(root.findtext("max_server_memory_usage"))
    assert cap < 1536 * 1024 * 1024
    assert int(root.findtext("mark_cache_size")) <= cap // 4


def test_every_system_log_expires():
    root = ET.parse(VPS / "clickhouse" / "config.d" / "system-logs.xml").getroot()
    for log in SYSTEM_LOGS:
        ttl = root.findtext(f"{log}/ttl")
        assert ttl and re.fullmatch(r"event_date \+ INTERVAL \d+ DAY DELETE", ttl), f"{log} has no TTL"


def test_env_example_names_every_variable_and_holds_no_value_for_secrets():
    lines = [line for line in (VPS / "env.example").read_text().splitlines() if line and not line.startswith("#")]
    values = dict(line.split("=", 1) for line in lines)
    for secret in ("ML_PG_PASSWORD", "ML_CH_PASSWORD", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        assert values[secret] == "", f"env.example carries a value for {secret}"
    assert values["CLICKHOUSE_PORT"] == "18123"
    assert "127.0.0.1:15432" in values["DATABASE_URL"]
