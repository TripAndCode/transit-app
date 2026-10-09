"""The retention periods the privacy policy states match the code that enforces them."""

from pathlib import Path

import pytest

from api.routers.auth import SESSION_TTL_DAYS
from pipeline.retention import (
    ADMIN_AUDIT_RETENTION_DAYS,
    PERSONAL_DATA_RETENTION_MONTHS,
    QUERY_LOG_RETENTION_DAYS,
)

LEGAL_DIR = Path(__file__).resolve().parents[2] / "frontend" / "public" / "legal"

PERIOD_ROWS = {
    "ja": [
        f"| セッション | 最長{SESSION_TTL_DAYS}日 |",
        f"| サインインの記録 | {PERSONAL_DATA_RETENTION_MONTHS}か月 |",
        f"| 日ごとの利用回数 | {PERSONAL_DATA_RETENTION_MONTHS}か月 |",
        f"| アカウントと結びつけない Ask の質問文 | {QUERY_LOG_RETENTION_DAYS}日 |",
        f"| 管理者の操作記録（あなたのアカウントに対する操作を含む） | {ADMIN_AUDIT_RETENTION_DAYS}日 |",
    ],
    "en": [
        f"| Sessions | Up to {SESSION_TTL_DAYS} days |",
        f"| Sign-in history | {PERSONAL_DATA_RETENTION_MONTHS} months |",
        f"| Daily usage counts | {PERSONAL_DATA_RETENTION_MONTHS} months |",
        f"| Ask questions not linked to your account | {QUERY_LOG_RETENTION_DAYS} days |",
        f"| The admin action log (including actions on your account) | {ADMIN_AUDIT_RETENTION_DAYS} days |",
    ],
}


@pytest.mark.parametrize("locale", ["ja", "en"])
def test_privacy_policy_states_the_enforced_periods(locale):
    policy = (LEGAL_DIR / f"privacy.{locale}.md").read_text(encoding="utf-8")
    missing = [row for row in PERIOD_ROWS[locale] if row not in policy]
    assert missing == []
