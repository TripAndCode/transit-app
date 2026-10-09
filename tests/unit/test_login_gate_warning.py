import logging

import api.main
from pipeline import flags


def test_boot_warns_when_sign_in_is_required_but_sso_is_unset(monkeypatch, caplog):
    monkeypatch.setattr(flags, "_load_overrides", lambda: {})
    monkeypatch.delenv("LOGIN_REQUIRED", raising=False)
    flags.reset_cache()
    with caplog.at_level(logging.WARNING, logger="api.main"):
        api.main._warn_if_login_gate_inactive(sso_enabled=False)
    assert "login_required is on but SSO is not configured" in caplog.text


def test_boot_is_quiet_when_sso_is_configured(monkeypatch, caplog):
    monkeypatch.setattr(flags, "_load_overrides", lambda: {})
    flags.reset_cache()
    with caplog.at_level(logging.WARNING, logger="api.main"):
        api.main._warn_if_login_gate_inactive(sso_enabled=True)
    assert "login_required is on" not in caplog.text
