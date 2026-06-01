from __future__ import annotations

import base64
import hashlib
import hmac
import os


class SecretCipherError(ValueError):
    """Raised when encrypted payload cannot be decrypted or verified."""


class SecretCipher:
    _VERSION = 1
    _NONCE_SIZE = 16
    _MAC_SIZE = 32

    def __init__(self, secret: str) -> None:
        normalized_secret = secret.strip()
        if not normalized_secret:
            raise ValueError("A non-empty secret is required for key encryption")
        self._key = hashlib.sha256(normalized_secret.encode("utf-8")).digest()

    def encrypt(self, plaintext: str) -> str:
        plaintext_bytes = plaintext.encode("utf-8")
        nonce = os.urandom(self._NONCE_SIZE)
        ciphertext = _xor_stream(plaintext_bytes, key=self._key, nonce=nonce)
        version = bytes([self._VERSION])
        mac = hmac.new(self._key, version + nonce + ciphertext, hashlib.sha256).digest()
        payload = version + nonce + mac + ciphertext
        return base64.urlsafe_b64encode(payload).decode("ascii")

    def decrypt(self, payload: str) -> str:
        try:
            raw_payload = base64.urlsafe_b64decode(payload.encode("ascii"))
        except Exception as exc:
            raise SecretCipherError("Encrypted payload is not valid base64") from exc

        minimum_size = 1 + self._NONCE_SIZE + self._MAC_SIZE
        if len(raw_payload) < minimum_size:
            raise SecretCipherError("Encrypted payload is too short")

        version = raw_payload[0]
        if version != self._VERSION:
            raise SecretCipherError("Encrypted payload version is not supported")

        nonce_start = 1
        nonce_end = nonce_start + self._NONCE_SIZE
        mac_end = nonce_end + self._MAC_SIZE
        nonce = raw_payload[nonce_start:nonce_end]
        mac = raw_payload[nonce_end:mac_end]
        ciphertext = raw_payload[mac_end:]
        expected_mac = hmac.new(self._key, bytes([version]) + nonce + ciphertext, hashlib.sha256).digest()
        if not hmac.compare_digest(mac, expected_mac):
            raise SecretCipherError("Encrypted payload integrity check failed")

        plaintext_bytes = _xor_stream(ciphertext, key=self._key, nonce=nonce)
        return plaintext_bytes.decode("utf-8")


def _xor_stream(payload: bytes, *, key: bytes, nonce: bytes) -> bytes:
    output = bytearray()
    counter = 0
    while len(output) < len(payload):
        counter_bytes = counter.to_bytes(4, byteorder="big", signed=False)
        block = hashlib.sha256(key + nonce + counter_bytes).digest()
        output.extend(block)
        counter += 1
    return bytes(current ^ stream for current, stream in zip(payload, output))
