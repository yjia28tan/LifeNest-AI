from __future__ import annotations

import datetime as dt
import time

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from google.auth import crypt, jwt

from app.auth import DevTokenVerifier, FirebaseTokenVerifier, InvalidTokenError

PROJECT = "lifenest-test"


def _key_and_cert() -> tuple[bytes, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")])
    now = dt.datetime.now(dt.UTC)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=1)).sign(key, hashes.SHA256()))
    pem_key = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    return pem_key, cert.public_bytes(serialization.Encoding.PEM).decode()


@pytest.fixture(scope="module")
def keys():
    good_key, good_cert = _key_and_cert()
    other_key, _ = _key_and_cert()
    return good_key, good_cert, other_key


def _token(private_key: bytes, **overrides) -> str:
    now = int(time.time())
    claims = {"iss": f"https://securetoken.google.com/{PROJECT}", "aud": PROJECT, "sub": "uid_123",
              "iat": now, "exp": now + 3600, "auth_time": now, **overrides}
    signer = crypt.RSASigner.from_string(private_key, key_id="kid1")
    return jwt.encode(signer, claims).decode()


@pytest.fixture
def verifier(keys) -> tuple[FirebaseTokenVerifier, list[int]]:
    calls: list[int] = []

    def fetch():
        calls.append(1)
        return {"kid1": keys[1]}, 3600.0

    return FirebaseTokenVerifier(PROJECT, fetch_certs=fetch), calls


async def test_valid_firebase_token(verifier, keys) -> None:
    v, calls = verifier
    assert await v.verify(_token(keys[0])) == "uid_123"
    assert await v.verify(_token(keys[0], sub="uid_456")) == "uid_456"
    assert len(calls) == 1  # certs cached


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "someone-else"},
        {"iss": "https://securetoken.google.com/someone-else"},
        {"exp": int(time.time()) - 600, "iat": int(time.time()) - 4000},
        {"sub": ""},
        {"sub": "../../admin"},
    ],
)
async def test_rejected_claims(verifier, keys, overrides) -> None:
    with pytest.raises(InvalidTokenError):
        await verifier[0].verify(_token(keys[0], **overrides))


async def test_wrong_signing_key_rejected(verifier, keys) -> None:
    with pytest.raises(InvalidTokenError):
        await verifier[0].verify(_token(keys[2]))


async def test_garbage_token_rejected(verifier) -> None:
    with pytest.raises(InvalidTokenError):
        await verifier[0].verify("not-a-jwt")


def test_firebase_requires_project() -> None:
    with pytest.raises(ValueError):
        FirebaseTokenVerifier("")


@pytest.mark.parametrize("token", ["dev:", "dev:a b", "prod:alice", "alice", "dev:" + "x" * 129])
async def test_dev_token_format(token) -> None:
    with pytest.raises(InvalidTokenError):
        await DevTokenVerifier().verify(token)
    assert await DevTokenVerifier().verify("dev:alice") == "alice"
