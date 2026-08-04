# demo_quantum_pqc.py

from vol1_encoding import encode_vote
from vol2_bb84 import get_secure_key
from vol3_pqc import encrypt_vote, decrypt_vote


def run_demo():
    print("=" * 60)
    print("QUANTUM-SAFE VOTING SYSTEM — LIVE DEMO")
    print("=" * 60)

    candidates = ["Aarav", "Diya", "Rohan", "Priya"]
    chosen_vote = "Diya"

    print(f"\nCandidates: {candidates}")
    print(f"Voter selects: {chosen_vote}")

    # Step 1: Quantum vote encoding
    print("\n--- STEP 1: Quantum Vote Encoding ---")
    vote_result = encode_vote(candidates, chosen_vote)
    print(f"Vote mapped to binary index : {vote_result['encoded_binary']}")
    print(f"Encoded into {vote_result['num_qubits']} qubit(s), gates applied, then measured")
    print(f"Measured qubit result       : {vote_result['measured_bits']}")
    print(f"Decoded back to candidate   : {vote_result['decoded_candidate']}")

    # Step 2: BB84 quantum key distribution
    print("\n--- STEP 2: BB84 Quantum Key Distribution ---")
    print("Simulating Aarav (sender) and Diya (receiver) exchanging qubits...")
    shared_key = get_secure_key(min_length=8)
    print(f"Shared secret key (8 bits)  : {shared_key}")
    print("(Any eavesdropping attempt during this exchange would be detectable")
    print(" because measuring a qubit in the wrong basis disturbs its state.)")

    # Step 3: PQC Encryption
    print("\n--- STEP 3: Quantum-Safe Encryption (PQC) ---")
    ciphertext = encrypt_vote(vote_result["decoded_candidate"], shared_key)
    print(f"Vote encrypted using key derived from BB84 output")
    print(f"Ciphertext                  : {ciphertext}")

    # Step 4: Storage placeholder (blockchain plugs in here)
    print("\n--- STEP 4: Secure Storage ---")
    print("[Ciphertext would be stored on the blockchain here]")
    print("(Blockchain module handles this — see blockchain/blockchain.py)")

    # Step 5: Decrypt and verify
    print("\n--- STEP 5: Verification (Decrypt & Display) ---")
    decrypted_vote = decrypt_vote(ciphertext, shared_key)
    print(f"Decrypted vote               : {decrypted_vote}")

    match = decrypted_vote == chosen_vote
    print(f"\nOriginal vote == Final result : {match}")

    print("END-TO-END FLOW COMPLETE" if match else "FLOW FAILED")



if __name__ == "__main__":
    run_demo()