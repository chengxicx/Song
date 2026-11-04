"""
At-rest encryption for AI session tokens.

The project stores most settings as plain text (the NetEase MUSIC_U
cookie included), and Lute has no cipher dependency.  AI session tokens
are bearer credentials for a user's own ChatGPT/Qwen/DeepSeek account,
so they get a small authenticated-encryption wrapper here instead:

- the key is derived from the app SECRET_KEY (persisted in
  <datapath>/.secret_key, see lute/app_factory.py), never from a
  constant in the repo;
- format is ``v1:<nonce hex>:<ciphertext hex>:<tag hex>``;
- encryption is an HMAC-SHA256 keystream (CTR-style) with a unique
  random nonce, authenticated with encrypt-then-MAC.

A changed SECRET_KEY makes old values undecryptable, at which point the
provider simply reads as "not configured" and the user re-binds.  That
is preferable to silently keeping credentials that no longer verify.
"""

import hashlib
import hmac
import os

from flask import current_app

_PREFIX = "v1:"
_LABEL = b"lute-ai-credential-v1"
_BLOCK = 32


def _key():
    "The 32-byte encryption key for the current app."
    secret = str(current_app.config.get("SECRET_KEY") or "")
    return hashlib.sha256(_LABEL + secret.encode("utf-8")).digest()


def _keystream(key, nonce, length):
    "HMAC-SHA256 blocks: block i = HMAC(key, nonce || i)."
    out = bytearray()
    counter = 0
    while len(out) < length:
        block = hmac.new(
            key, nonce + counter.to_bytes(8, "big"), hashlib.sha256
        ).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:length])


def _xor(key, nonce, data):
    return bytes(a ^ b for a, b in zip(data, _keystream(key, nonce, len(data))))


def encrypt(plaintext):
    "Encrypt a credential string; '' stays ''."
    text = plaintext or ""
    if text == "":
        return ""
    key = _key()
    nonce = os.urandom(16)
    ciphertext = _xor(key, nonce, text.encode("utf-8"))
    tag = hmac.new(key, nonce + ciphertext, hashlib.sha256).digest()
    return f"{_PREFIX}{nonce.hex()}:{ciphertext.hex()}:{tag.hex()}"


def decrypt(blob):
    "Decrypt a value from encrypt(); returns '' for anything invalid."
    if not blob or not blob.startswith(_PREFIX):
        return ""
    try:
        nonce_hex, ciphertext_hex, tag_hex = blob[len(_PREFIX) :].split(":")
        nonce = bytes.fromhex(nonce_hex)
        ciphertext = bytes.fromhex(ciphertext_hex)
        tag = bytes.fromhex(tag_hex)
        key = _key()
        expected = hmac.new(key, nonce + ciphertext, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, tag):
            return ""
        return _xor(key, nonce, ciphertext).decode("utf-8")
    except (ValueError, TypeError):
        return ""
