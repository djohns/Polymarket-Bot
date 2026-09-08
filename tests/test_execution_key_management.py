import pytest

from polybot.execution.key_management import (
    KeyManagementError,
    encrypt_private_key_to_file,
    load_private_key,
)

RAW_KEY = "0xdeadbeef" * 8
ENV_VAR = "TEST_POLYMARKET_KEY_PASSPHRASE"


def test_encrypt_and_decrypt_round_trip(tmp_path, monkeypatch):
    out_path = str(tmp_path / "key.enc")
    monkeypatch.setenv(ENV_VAR, "correct horse battery staple")

    encrypt_private_key_to_file(RAW_KEY, out_path, "correct horse battery staple")
    decrypted = load_private_key(out_path, ENV_VAR)

    assert decrypted == RAW_KEY


def test_wrong_passphrase_fails(tmp_path, monkeypatch):
    out_path = str(tmp_path / "key.enc")
    encrypt_private_key_to_file(RAW_KEY, out_path, "the-real-passphrase")
    monkeypatch.setenv(ENV_VAR, "not-the-real-passphrase")

    with pytest.raises(KeyManagementError):
        load_private_key(out_path, ENV_VAR)


def test_missing_env_var_fails(tmp_path, monkeypatch):
    out_path = str(tmp_path / "key.enc")
    encrypt_private_key_to_file(RAW_KEY, out_path, "whatever")
    monkeypatch.delenv(ENV_VAR, raising=False)

    with pytest.raises(KeyManagementError):
        load_private_key(out_path, ENV_VAR)
