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
        "| ログインと主な操作の記録（管理者用のパスワードでのログインの失敗を含みます） | "
        f"{PERSONAL_DATA_RETENTION_MONTHS}か月 |",
        f"| 管理者の日ごとの利用回数 | {PERSONAL_DATA_RETENTION_MONTHS}か月 |",
        f"| 質問文 | 最後に質問されてから{QUERY_LOG_RETENTION_DAYS}日 |",
        f"| 管理者の操作記録 | {ADMIN_AUDIT_RETENTION_DAYS}日 |",
    ],
    "en": [
        f"| Sessions | Up to {SESSION_TTL_DAYS} days |",
        "| Sign-in and key-action history, including failed administrator password sign-ins | "
        f"{PERSONAL_DATA_RETENTION_MONTHS} months |",
        f"| An administrator's daily usage counts | {PERSONAL_DATA_RETENTION_MONTHS} months |",
        f"| Questions | {QUERY_LOG_RETENTION_DAYS} days after a question was last asked |",
        f"| The admin action log | {ADMIN_AUDIT_RETENTION_DAYS} days |",
    ],
}


@pytest.mark.parametrize("locale", ["ja", "en"])
def test_privacy_policy_states_the_enforced_periods(locale):
    policy = (LEGAL_DIR / f"privacy.{locale}.md").read_text(encoding="utf-8")
    missing = [row for row in PERIOD_ROWS[locale] if row not in policy]
    assert missing == []
