"""Login tokens (JWT) for dashboard users.

A token holds the user ID (`sub`), when it was made (`iat`) and when it expires (`exp`).
It is signed with HS256 and JWT_SECRET, so nobody can change it without the secret.
"""

import uuid
from datetime import UTC, datetime, timedelta

import jwt
from pydantic import SecretStr

ALGORITHM = "HS256"


def create_access_token(user_id: uuid.UUID, secret: SecretStr, lifetime: timedelta) -> str:
    now = datetime.now(UTC)
    payload = {"sub": str(user_id), "iat": now, "exp": now + lifetime}
    return jwt.encode(payload, secret.get_secret_value(), algorithm=ALGORITHM)


def read_access_token(token: str, secret: SecretStr) -> uuid.UUID:
    """Check the signature and the expiry time, and return the user ID.

    Raises `jwt.InvalidTokenError` if the token is wrong, changed or expired.
    """
    payload = jwt.decode(
        token,
        secret.get_secret_value(),
        algorithms=[ALGORITHM],  # never trust the algorithm named in the token itself
        options={"require": ["sub", "iat", "exp"]},
    )
    try:
        return uuid.UUID(payload["sub"])
    except ValueError as exc:
        raise jwt.InvalidTokenError("The token subject is not a user ID.") from exc
