# vol3_pqc.py
"""
Symmetric Ballot Encryption and Key Derivation.

CRITICAL ARCHITECTURAL NOTE:
----------------------------
This module implements ballot encryption using standard symmetric cryptography
(AES-128 in CBC mode with HMAC-SHA256 via the Fernet specification).
It is NOT post-quantum public-key cryptography (PQC) such as NIST FIPS 203 ML-KEM
(Kyber) or FIPS 204 ML-DSA (Dilithium).

Keys are established via simulated BB84 QKD (vol2) and derived using standard
HKDF-SHA256 with domain separation context. A 256-bit symmetric key provides
128-bit quantum security against Grover's search algorithm.
"""

import base64
from typing import Any
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
from cryptography.fernet import Fernet


# Domain separation context string for voting ballot encryption
BALLOT_ENCRYPTION_INFO = b"ipd-voting-system:ballot-encryption:v1"


def _bits_to_bytes(bit_list: list[int]) -> bytes:
    """
    Packs a list of 0/1 bits into bytes (MSB first).
    If the number of bits is not a multiple of 8, the final byte is left-shifted.
    """
    byte_arr = bytearray()
    for i in range(0, len(bit_list), 8):
        chunk = bit_list[i : i + 8]
        val = 0
        for bit in chunk:
            val = (val << 1) | (1 if bit else 0)
        if len(chunk) < 8:
            val <<= 8 - len(chunk)
        byte_arr.append(val)
    return bytes(byte_arr)


def derive_aes_key(bb84_key: Any, info: bytes = BALLOT_ENCRYPTION_INFO) -> bytes:
    """
    Derives a Fernet (AES-128-CBC + HMAC-SHA256) key from BB84 shared key material
    using standard HKDF-SHA256 with explicit domain separation.

    Args:
        bb84_key: List of bits (0 and 1) or raw bytes from BB84 exchange.
        info: Context and application specific information for HKDF domain separation.

    Returns:
        32-byte URL-safe base64-encoded key required by Fernet.
    """
    if isinstance(bb84_key, (bytes, bytearray)):
        key_material = bytes(bb84_key)
    elif isinstance(bb84_key, list):
        key_material = _bits_to_bytes(bb84_key)
    else:
        raise TypeError(f"bb84_key must be a list of bits or bytes, got {type(bb84_key)}")

    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=info,
    )
    derived = hkdf.derive(key_material)
    return base64.urlsafe_b64encode(derived)


def encrypt_vote(vote_data: str, bb84_key: Any, info: bytes = BALLOT_ENCRYPTION_INFO) -> bytes:
    """
    Encrypts the vote data using a Fernet key derived via HKDF-SHA256 from the BB84 shared key.
    """
    aes_key = derive_aes_key(bb84_key, info=info)
    fernet = Fernet(aes_key)
    return fernet.encrypt(vote_data.encode("utf-8"))


def decrypt_vote(ciphertext: bytes, bb84_key: Any, info: bytes = BALLOT_ENCRYPTION_INFO) -> str:
    """
    Decrypts the ciphertext back into the original vote data using the same BB84 key.
    """
    aes_key = derive_aes_key(bb84_key, info=info)
    fernet = Fernet(aes_key)
    return fernet.decrypt(ciphertext).decode("utf-8")


if __name__ == "__main__":
    # Demo with a 256-bit BB84 key
    sample_key = [1, 0] * 128
    vote = "Diya"

    print("Original vote:  ", vote)
    ciphertext = encrypt_vote(vote, sample_key)
    print("Encrypted vote: ", ciphertext)

    decrypted = decrypt_vote(ciphertext, sample_key)
    print("Decrypted vote: ", decrypted)
    print("Match:          ", vote == decrypted)