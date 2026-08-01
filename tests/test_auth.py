from app.auth import token_ok


def test_token_ok_match():
    assert token_ok("secret", "secret") is True


def test_token_ok_mismatch():
    assert token_ok("secret", "other") is False


def test_token_ok_empty_rejected():
    assert token_ok("", "") is False
    assert token_ok("secret", "") is False
    assert token_ok("", "secret") is False
