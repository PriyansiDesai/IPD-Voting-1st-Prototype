# vol3_pqc.py

import hashlib
from cryptography.fernet import Fernet
import base64


def derive_aes_key(bb84_key: list) -> bytes:
    """
    Takes the small BB84 key (list of 0s and 1s) and stretches it
    into a proper 256-bit AES key using a cryptographic hash function.
    """
    # Convert the bit list into a string, e.g. [1,0,1,1] -> "1011"
    bit_string = ''.join(str(bit) for bit in bb84_key)

    # Hash it into a fixed 32-byte (256-bit) key using SHA-256
    hashed = hashlib.sha256(bit_string.encode()).digest()

    # Fernet (the encryption library we're using) needs a base64-encoded key
    fernet_key = base64.urlsafe_b64encode(hashed)

    return fernet_key


def encrypt_vote(vote_data: str, bb84_key: list) -> bytes:
    """
    Encrypts the vote data using a key derived from the BB84 shared key.
    """
    aes_key = derive_aes_key(bb84_key)
    fernet = Fernet(aes_key)
    ciphertext = fernet.encrypt(vote_data.encode())
    return ciphertext


def decrypt_vote(ciphertext: bytes, bb84_key: list) -> str:
    """
    Decrypts the ciphertext back into the original vote data,
    using the same BB84 key.
    """
    aes_key = derive_aes_key(bb84_key)
    fernet = Fernet(aes_key)
    plaintext = fernet.decrypt(ciphertext).decode()
    return plaintext


if __name__ == "__main__":
    # Demo: encrypt and decrypt a sample vote
    sample_key = [1, 0, 1, 1, 0, 0, 1, 0]  # pretend this came from BB84
    vote = "Diya"

    print("Original vote:  ", vote)

    ciphertext = encrypt_vote(vote, sample_key)
    print("Encrypted vote: ", ciphertext)

    decrypted = decrypt_vote(ciphertext, sample_key)
    print("Decrypted vote: ", decrypted)

    print("Match:          ", vote == decrypted)