"""Which ``api_keys`` rows belong to a user.

A key belongs to the account it was issued to (``owner_user_id``), or, for a
legacy ownerless key inserted by hand with only an address, to the account
holding that address. The address comparison ignores case: an address is one
identity whatever its case, and an ownerless key keeps the casing it was
inserted with. Account erasure, account export and the admin soft-delete all
act on this one set.
"""

#: SQL predicate over ``api_keys``. ``$1`` is the user's ``user_id`` and ``$2``
#: their email. Parenthesized, so it can be ANDed with other conditions.
OWNED_BY_USER = "(owner_user_id = $1 OR (owner_user_id IS NULL AND lower(owner_email) = lower($2)))"
