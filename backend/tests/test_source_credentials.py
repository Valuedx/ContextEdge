import pytest
from cryptography.fernet import Fernet

from contextedge.services import source_service
from contextedge.services.source_service import (
    CredentialEncryptionUnavailable,
    decrypt_credentials,
    encrypt_credentials,
)


async def test_round_trip_with_real_key(monkeypatch):
    monkeypatch.setattr(
        source_service.settings, "fernet_key", Fernet.generate_key().decode()
    )
    encrypted = await encrypt_credentials({"token": "secret-value"})
    assert await decrypt_credentials(encrypted) == {"token": "secret-value"}


async def test_empty_key_raises_instead_of_transient_key(monkeypatch):
    monkeypatch.setattr(source_service.settings, "fernet_key", "")
    with pytest.raises(CredentialEncryptionUnavailable, match="FERNET_KEY"):
        await encrypt_credentials({"token": "secret-value"})


async def test_placeholder_key_raises(monkeypatch):
    monkeypatch.setattr(source_service.settings, "fernet_key", "change-me-please")
    with pytest.raises(CredentialEncryptionUnavailable):
        await encrypt_credentials({"token": "x"})


async def test_malformed_key_raises(monkeypatch):
    monkeypatch.setattr(source_service.settings, "fernet_key", "not-base64!!")
    with pytest.raises(CredentialEncryptionUnavailable, match="malformed"):
        await decrypt_credentials(b"gAAAAAB...")
