"""Password hashing with argon2id.

argon2 is slow on purpose and needs a lot of memory, so stolen hashes are very hard to crack.
One hash takes ~50-100 ms of CPU: call these functions with `asyncio.to_thread`.
"""

from functools import cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()  # the library defaults follow RFC 9106


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """True if `password` matches the hash. A wrong password returns False (no exception)."""
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """True if the hash was made with older settings: save a new one at the next login."""
    return _hasher.check_needs_rehash(password_hash)


def verify_dummy_password(password: str) -> None:
    """Take as long as a real check. Login calls this when the email is unknown, so an
    attacker cannot find out which emails exist by measuring the response time."""
    verify_password(_dummy_hash(), password)


@cache
def _dummy_hash() -> str:
    return hash_password("not anyone's password")
