"""SQL string-building helpers shared across routers."""


def escape_like(s: str) -> str:
    """Escape ``s`` for safe interpolation into a ``LIKE``/``ILIKE`` pattern.

    Escapes the backslash first, then the two characters that carry special
    meaning in a LIKE pattern (``%``, ``_``), so a caller-supplied substring
    can never smuggle in its own wildcard. Pair with ``ESCAPE '\\'`` in the
    SQL so Postgres treats a literal ``\\`` as the escape character.
    """
    return s.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
