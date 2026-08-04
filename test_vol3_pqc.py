# test_vol3_pqc.py

from vol3_pqc import encrypt_vote, decrypt_vote
from cryptography.fernet import InvalidToken

def run_all_tests():
    print("=== Test A: Encrypt then decrypt returns original vote ===")
    key = [1, 0, 1, 1, 0, 0, 1, 0]
    votes = ["Aarav", "Diya", "Rohan", "Priya"]
    passed = True
    for vote in votes:
        ciphertext = encrypt_vote(vote, key)
        decrypted = decrypt_vote(ciphertext, key)
        if decrypted != vote:
            passed = False
            print(f"  FAIL: {vote} -> {decrypted}")
    print("  PASSED" if passed else "  FAILED")

    print("\n=== Test B: Ciphertext is not readable / not equal to plaintext ===")
    vote = "Diya"
    ciphertext = encrypt_vote(vote, key)
    passed = vote.encode() not in ciphertext
    print("  PASSED" if passed else "  FAILED")

    print("\n=== Test C: Wrong key should fail to decrypt ===")
    correct_key = [1, 0, 1, 1, 0, 0, 1, 0]
    wrong_key =   [0, 1, 0, 0, 1, 1, 0, 1]  # different key

    ciphertext = encrypt_vote("Diya", correct_key)
    try:
        decrypt_vote(ciphertext, wrong_key)
        print("  FAILED: decryption should NOT have succeeded with wrong key")
    except InvalidToken:
        print("  PASSED: correctly rejected wrong key")

    print("\n=== Test D: Different votes produce different ciphertexts ===")
    c1 = encrypt_vote("Diya", key)
    c2 = encrypt_vote("Rohan", key)
    status = "OK" if c1 != c2 else "FAIL"
    print(f"  Different ciphertexts for different votes [{status}]")


if __name__ == "__main__":
    run_all_tests()