# test_vol4_blockchain.py

from vol4_blockchain import Block, Blockchain


def run_all_tests():

    # ── Test A: Genesis block is created automatically ────────────────────────
    print("=== Test A: Genesis block exists and is index 0 ===")
    bc = Blockchain()
    assert len(bc.chain) == 1, "Chain should start with exactly one block (genesis)"
    assert bc.chain[0].index == 0, "Genesis block must have index 0"
    assert bc.chain[0].vote_data["voter_id"] == "GENESIS", "Genesis voter_id must be 'GENESIS'"
    assert bc.chain[0].prev_hash == "0" * 64, "Genesis prev_hash must be 64 zeroes"
    print("  PASSED")

    # ── Test B: Adding a vote creates a correctly linked block ────────────────
    print("\n=== Test B: Adding a vote appends a linked block ===")
    bc = Blockchain()
    block = bc.add_vote(voter_id="V001", encrypted_vote="a9d72f8c" * 8)
    assert block.index == 1, "First vote block must have index 1"
    assert block.prev_hash == bc.chain[0].hash, "Block must point to genesis hash"
    assert block.vote_data["voter_id"] == "V001"
    assert block.vote_data["encrypted_vote"] == "a9d72f8c" * 8
    print("  PASSED")

    # ── Test C: Hash is consistent with block contents ────────────────────────
    print("\n=== Test C: Block hash is deterministic and consistent ===")
    bc = Blockchain()
    bc.add_vote("V001", "deadbeef" * 8)
    for blk in bc.chain:
        recomputed = blk._compute_hash()
        assert blk.hash == recomputed, f"Hash mismatch on block {blk.index}"
    print("  PASSED")

    # ── Test D: A valid chain passes is_chain_valid() ─────────────────────────
    print("\n=== Test D: Valid chain is reported as valid ===")
    bc = Blockchain()
    bc.add_vote("V001", "aaa" * 20)
    bc.add_vote("V002", "bbb" * 20)
    bc.add_vote("V003", "ccc" * 20)
    assert bc.is_chain_valid() is True, "A freshly built chain must be valid"
    print("  PASSED")

    # ── Test E: Tampering with vote_data is detected ──────────────────────────
    print("\n=== Test E: Altering vote_data breaks chain validity ===")
    bc = Blockchain()
    bc.add_vote("V001", "original_payload" * 4)
    bc.chain[1].vote_data["encrypted_vote"] = "tampered_payload"
    # Note: we do NOT call recompute_hash() — that would be the attacker 'fixing' the hash
    assert bc.is_chain_valid() is False, "Tampered block must be detected as invalid"
    print("  PASSED")

    # ── Test F: Tampering with the hash itself is detected ────────────────────
    print("\n=== Test F: Altering a block's hash directly breaks chain validity ===")
    bc = Blockchain()
    bc.add_vote("V001", "realvote" * 8)
    bc.add_vote("V002", "anothervote" * 5)
    # Corrupt the hash of block 1 and patch prev_hash of block 2 to match —
    # this simulates a sophisticated attacker; the hash still won't match contents.
    bc.chain[1].hash = "0" * 64
    bc.chain[2].prev_hash = "0" * 64
    assert bc.is_chain_valid() is False, "Manipulated hashes must be detected"
    print("  PASSED")

    # ── Test G: Block count is correct ───────────────────────────────────────
    print("\n=== Test G: get_vote_count() returns correct count ===")
    bc = Blockchain()
    assert bc.get_vote_count() == 0
    bc.add_vote("V001", "x" * 64)
    assert bc.get_vote_count() == 1
    bc.add_vote("V002", "y" * 64)
    assert bc.get_vote_count() == 2
    print("  PASSED")

    # ── Test H: Double-vote detection ────────────────────────────────────────
    print("\n=== Test H: has_voter_voted() correctly identifies repeat voters ===")
    bc = Blockchain()
    bc.add_vote("V001", "vote1" * 12)
    bc.add_vote("V002", "vote2" * 12)
    assert bc.has_voter_voted("V001") is True,  "V001 should be flagged as already voted"
    assert bc.has_voter_voted("V002") is True,  "V002 should be flagged as already voted"
    assert bc.has_voter_voted("V099") is False, "V099 has not voted, should return False"
    assert bc.has_voter_voted("GENESIS") is False, "GENESIS is skipped; should return False"
    print("  PASSED")

    # ── Test I: export_chain() returns dicts with expected keys ───────────────
    print("\n=== Test I: export_chain() produces correct structure ===")
    bc = Blockchain()
    bc.add_vote("V001", "ab12cd34" * 8, timestamp="2026-07-13T10:30:00")
    exported = bc.export_chain()
    expected_keys = {"index", "timestamp", "vote_data", "prev_hash", "hash"}
    for entry in exported:
        assert expected_keys == set(entry.keys()), f"Unexpected keys: {set(entry.keys())}"
    assert exported[1]["vote_data"]["voter_id"] == "V001"
    assert exported[1]["vote_data"]["timestamp"] == "2026-07-13T10:30:00"
    print("  PASSED")

    # ── Test J: Chain grows correctly with multiple votes ────────────────────
    print("\n=== Test J: Hash chain links are all correct across 5 blocks ===")
    bc = Blockchain()
    voters = [f"V{str(i).zfill(3)}" for i in range(1, 6)]
    for v in voters:
        bc.add_vote(v, f"payload_{v}" * 4)

    assert len(bc.chain) == 6   # genesis + 5 votes
    for i in range(1, len(bc.chain)):
        assert bc.chain[i].prev_hash == bc.chain[i - 1].hash, \
            f"Hash link broken at block {i}"
    assert bc.is_chain_valid() is True
    print("  PASSED")

    # ─────────────────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  ALL TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    run_all_tests()
