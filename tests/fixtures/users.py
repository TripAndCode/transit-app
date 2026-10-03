"""`User` values for tests that stand in for the auth dependencies."""

from __future__ import annotations

from api.security import User


def admin_user(user_id: int = 1) -> User:
    """An active, LLM-approved admin, as `require_admin` resolves one.

    `user_id` is a parameter for tests that assert the acting admin's id was
    recorded: an id distinct from every other id in the test (an agency's, a
    target user's) is what lets that assertion tell them apart.
    """
    return User(
        user_id=user_id,
        email="admin@example.com",
        name="Admin",
        avatar_url=None,
        role="admin",
        suspended_at=None,
        llm_approved=True,
    )
