# demo1.py
# Full end-to-end demo: Quantum Encoding -> BB84 QKD -> PQC Encryption -> Blockchain -> Verify

from vol1_encoding import encode_vote
from vol2_bb84 import get_secure_key
from vol3_pqc import encrypt_vote, decrypt_vote
from vol4_blockchain import Blockchain


def run_demo():
    print("=" * 60)
    print("QUANTUM-SAFE VOTING SYSTEM -- LIVE DEMO")
    print("=" * 60)

    candidates = ["Aarav", "Diya", "Rohan", "Priya"]
    voter_id   = "V001"
    chosen_vote = "Diya"

    print(f"\nVoter ID   : {voter_id}")
    print(f"Candidates : {candidates}")
    print(f"Voter selects: {chosen_vote}")

    # ── STEP 1: Quantum Vote Encoding ────────────────────────────────────────
    print("\n--- STEP 1: Quantum Vote Encoding ---")
    vote_result = encode_vote(candidates, chosen_vote)
    print(f"Vote mapped to binary index : {vote_result['encoded_binary']}")
    print(f"Encoded into {vote_result['num_qubits']} qubit(s), gates applied, then measured")
    print(f"Measured qubit result       : {vote_result['measured_bits']}")
    print(f"Decoded back to candidate   : {vote_result['decoded_candidate']}")

    # ── STEP 2: BB84 Quantum Key Distribution ────────────────────────────────
    print("\n--- STEP 2: BB84 Quantum Key Distribution ---")
    print("Simulating Aarav (sender) and Diya (receiver) exchanging qubits...")
    shared_key = get_secure_key(min_length=8)
    print(f"Shared secret key (8 bits)  : {shared_key}")
    print("(Any eavesdropping attempt during this exchange would be detectable")
    print(" because measuring a qubit in the wrong basis disturbs its state.)")

    # ── STEP 3: PQC Encryption ───────────────────────────────────────────────
    print("\n--- STEP 3: Quantum-Safe Encryption (PQC) ---")
    ciphertext_bytes = encrypt_vote(vote_result["decoded_candidate"], shared_key)
    # Convert bytes -> hex string so the blockchain can store it as plain text
    ciphertext_hex = ciphertext_bytes.hex()
    print(f"Vote encrypted using key derived from BB84 output")
    print(f"Ciphertext (hex, first 64 chars) : {ciphertext_hex[:64]}...")

    # ── STEP 4: Store on Blockchain ──────────────────────────────────────────
    print("\n--- STEP 4: Storing Encrypted Vote on Blockchain ---")
    bc = Blockchain()
    block = bc.add_vote(voter_id=voter_id, encrypted_vote=ciphertext_hex)
    print(f"Block created   : index={block.index}")
    print(f"Block hash      : {block.hash[:32]}...")
    print(f"Prev hash       : {block.prev_hash[:32]}...")
    print(f"Chain length    : {len(bc.chain)} block(s) (including genesis)")
    print(f"Chain valid     : {bc.is_chain_valid()}")

    # ── STEP 5: Retrieve from Chain, Decrypt & Verify ────────────────────────
    print("\n--- STEP 5: Retrieve from Chain -> Decrypt -> Verify ---")

    # Retrieve the encrypted payload straight from the chain
    retrieved_hex   = bc.chain[block.index].vote_data["encrypted_vote"]
    retrieved_bytes = bytes.fromhex(retrieved_hex)

    decrypted_vote = decrypt_vote(retrieved_bytes, shared_key)
    print(f"Retrieved from blockchain : {retrieved_hex[:64]}...")
    print(f"Decrypted vote            : {decrypted_vote}")

    match = decrypted_vote == chosen_vote
    print(f"\nOriginal vote == Final result : {match}")

    # ── Final chain view ─────────────────────────────────────────────────────
    print()
    bc.view_chain()

    print("\n" + ("END-TO-END FLOW COMPLETE" if match else "FLOW FAILED"))


if __name__ == "__main__":
    run_demo()