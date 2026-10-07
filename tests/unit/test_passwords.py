"""Tests for password hashing (argon2id)."""

from api.auth.passwords import hash_password, needs_rehash, verify_password


def test_the_right_password_matches_and_a_wrong_one_does_not() -> None:
    password_hash = hash_password("correct horse battery")

    assert password_hash.startswith("$argon2id$")
    assert "correct horse battery" not in password_hash
    assert verify_password(password_hash, "correct horse battery")
    assert not verify_password(password_hash, "wrong password")


def test_the_same_password_gets_a_different_hash_each_time() -> None:
    # A random salt in each hash: two users with the same password have different hashes.
    assert hash_password("same password") != hash_password("same password")


def test_a_broken_hash_never_matches() -> None:
    assert not verify_password("not-an-argon2-hash", "anything")


def test_new_hashes_do_not_need_a_rehash() -> None:
    assert not needs_rehash(hash_password("a password"))
