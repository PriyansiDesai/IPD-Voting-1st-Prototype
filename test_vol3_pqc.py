# test_vol3_pqc.py
"""
Test Suite for Vol3 Symmetric Ballot Encryption and Key Derivation.

Verifies:
- Round-trip encryption and decryption using HKDF-SHA256 derived keys
- Ciphertext confidentiality (plaintext not visible in ciphertext)
- Authentication/integrity: wrong key rejection with InvalidToken
- Semantic security under distinct votes
- Domain separation in HKDF key derivation
- Compatibility with 256-bit keys and legacy key representations
"""

import unittest
from cryptography.fernet import InvalidToken
from vol3_pqc import (
    encrypt_vote,
    decrypt_vote,
    derive_aes_key,
    BALLOT_ENCRYPTION_INFO,
)


class TestVol3PQC(unittest.TestCase):
    """Unit test suite for symmetric ballot encryption via HKDF-SHA256 and Fernet."""

    def test_encrypt_decrypt_roundtrip_256bit_key(self):
        """Encrypt then decrypt returns original vote with standard 256-bit key."""
        key_256 = [1, 0] * 128
        votes = ["Aarav", "Diya", "Rohan", "Priya"]
        for vote in votes:
            ciphertext = encrypt_vote(vote, key_256)
            decrypted = decrypt_vote(ciphertext, key_256)
            self.assertEqual(decrypted, vote)

    def test_legacy_short_key_compatibility(self):
        """Maintains backward compatibility with byte packing from shorter bit lists."""
        short_key = [1, 0, 1, 1, 0, 0, 1, 0]
        ciphertext = encrypt_vote("Diya", short_key)
        decrypted = decrypt_vote(ciphertext, short_key)
        self.assertEqual(decrypted, "Diya")

    def test_ciphertext_confidentiality(self):
        """Ciphertext is encrypted and does not contain plaintext bytes."""
        key = [1, 0] * 128
        vote = "Diya"
        ciphertext = encrypt_vote(vote, key)
        self.assertNotIn(vote.encode("utf-8"), ciphertext)

    def test_wrong_key_fails_to_decrypt(self):
        """Tampered or mismatched keys are rejected with InvalidToken."""
        correct_key = [1, 0] * 128
        wrong_key = [0, 1] * 128

        ciphertext = encrypt_vote("Diya", correct_key)
        with self.assertRaises(InvalidToken):
            decrypt_vote(ciphertext, wrong_key)

    def test_different_votes_produce_different_ciphertexts(self):
        """Different plaintexts produce distinct ciphertexts."""
        key = [1, 0] * 128
        c1 = encrypt_vote("Diya", key)
        c2 = encrypt_vote("Rohan", key)
        self.assertNotEqual(c1, c2)

    def test_hkdf_domain_separation(self):
        """Different HKDF info contexts derive cryptographically distinct keys."""
        key = [1, 0] * 128
        k1 = derive_aes_key(key, info=b"context-alpha")
        k2 = derive_aes_key(key, info=b"context-beta")
        k_default = derive_aes_key(key, info=BALLOT_ENCRYPTION_INFO)

        self.assertNotEqual(k1, k2)
        self.assertNotEqual(k1, k_default)
        self.assertEqual(len(k_default), 44)  # 32 bytes base64 encoded = 44 ASCII chars


def run_all_tests():
    """Runs tests and prints formatted summary for standalone CLI execution."""
    loader = unittest.defaultTestLoader
    suite = loader.loadTestsFromTestCase(TestVol3PQC)
    runner = unittest.TextTestRunner(verbosity=2)
    return runner.run(suite)


if __name__ == "__main__":
    unittest.main()