"""Invariants of the VPS replica stack. The replica holds a copy of production
history, so its listeners stay on loopback, its memory stays inside the box,
and nothing it owns can be mistaken for the dev stores the guard hook protects."""

from __future__ import annotations

import importlib.util
import os
import re
import shlex
import subprocess
import xml.etree.ElementTree as ET
from itertools import pairwise
from pathlib import Path
from types import ModuleType

import yaml

ROOT = Path(__file__).resolve().parents[2]
VPS = ROOT / "deploy" / "vps"


def _load_guard() -> ModuleType:
    spec = importlib.util.spec_from_file_location("guard_dev_db", ROOT / ".claude" / "hooks" / "guard_dev_db.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GUARD = _load_guard()
# Read from their canonical homes, the guard hook and the dev stack, so a
# port or volume added there is held off the replica too.
DEV_PORTS = tuple(GUARD.DEV_PORTS)
DEV_VOLUMES = tuple(yaml.safe_load((ROOT / "compose.yml").read_text())["volumes"])
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


def test_postgres_is_healthy_only_once_it_listens_on_tcp():
    # The image's first run serves initdb from a socket-only server; a socket
    # check passes then, and bootstrap's migrate over TCP is refused.
    check = shlex.split(_compose()["services"]["ml-pg"]["healthcheck"]["test"][1])
    assert check[0] == "pg_isready"
    assert ("-h", "localhost") in pairwise(check)


def test_the_guard_hook_lets_writes_reach_the_replica():
    # The hook knows dev targets by service and container name; a replica
    # service sharing one would be refused as if it were the dev store.
    write = "clickhouse-client --query 'INSERT INTO t VALUES (1)'"
    for service in _compose()["services"]:
        for cmd in (
            f"docker compose -f deploy/vps/compose.yml exec {service} {write}",
            f"docker exec transit-ml-{service}-1 {write}",
        ):
            assert not GUARD.should_block(cmd), cmd


def test_both_services_carry_a_memory_limit():
    services = _compose()["services"]
    assert services["ml-ch"]["mem_limit"] == "1536m"
    assert services["ml-pg"]["mem_limit"] == "640m"


def test_volumes_cannot_be_mistaken_for_dev_stores():
    names = set(_compose()["volumes"])
    assert names == {"ml_pg", "ml_ch"}
    assert not any(dev in name for dev in DEV_VOLUMES for name in names)


def test_clickhouse_image_matches_ci():
    ci = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    ci_image = ci["jobs"]["test"]["services"]["clickhouse"]["image"]
    assert _compose()["services"]["ml-ch"]["image"] == ci_image


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


def _fake_curl(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl = bin_dir / "curl"
    curl.write_text(f'#!/bin/sh\necho "$@" >> {tmp_path / "pings"}\n')
    curl.chmod(0o755)
    return bin_dir


def _ping_run(tmp_path, job, url):
    env = {**os.environ, "PATH": f"{_fake_curl(tmp_path)}:{os.environ['PATH']}"}
    if url:
        env["HC_PING_URL_SYNC"] = url
    result = subprocess.run(
        ["bash", str(VPS / "run-with-ping.sh"), "SYNC", "sh", "-c", job], env=env, capture_output=True, text=True
    )
    pings = (tmp_path / "pings").read_text().splitlines() if (tmp_path / "pings").exists() else []
    return result.returncode, pings


def test_a_succeeding_job_pings_start_then_success(tmp_path):
    status, pings = _ping_run(tmp_path, "echo ok", "https://hc.example/abc")
    assert status == 0
    assert pings[0].endswith("https://hc.example/abc/start")
    assert pings[-1].endswith("https://hc.example/abc")


def test_a_failing_job_keeps_its_status_and_pings_fail(tmp_path):
    status, pings = _ping_run(tmp_path, "echo boom; exit 3", "https://hc.example/abc")
    assert status == 3
    assert pings[-1].endswith("https://hc.example/abc/fail")


def test_without_a_ping_url_the_job_runs_unreported(tmp_path):
    status, pings = _ping_run(tmp_path, "exit 0", None)
    assert status == 0 and pings == []


def _unit(name: str) -> str:
    return (VPS / "systemd" / name).read_text()


def test_the_sync_runs_in_jst_after_the_collector_has_verified_r2():
    calendar = re.search(r"^OnCalendar=(.+)$", _unit("transit-ml-sync.timer"), re.MULTILINE)[1]
    assert calendar.endswith("Asia/Tokyo")
    hour, minute = map(int, re.search(r"(\d{2}):(\d{2})", calendar).groups())
    assert (hour, minute) > (9, 15)


def test_the_sync_unit_runs_the_cli_through_the_ping_wrapper():
    exec_start = re.search(r"^ExecStart=(.+)$", _unit("transit-ml-sync.service"), re.MULTILINE)[1]
    assert "deploy/vps/run-with-ping.sh SYNC" in exec_start
    assert "-m ml.cli sync" in exec_start


def test_the_bootstrap_script_parses():
    assert subprocess.run(["bash", "-n", str(VPS / "bootstrap.sh")]).returncode == 0
