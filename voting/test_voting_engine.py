"""
test_voting_engine.py
M3: Test suite for Multi-Voter Voting Engine with Quantum & Blockchain Pipeline.

Covers:
1. Successful end-to-end vote (quantum encoding, BB84 key, PQC encryption, blockchain block)
2. Invalid/unknown voter rejected
3. Ineligible voter rejected
4. Inactive session rejected (COMPLETED and UPCOMING)
5. Invalid/unknown candidate rejected
6. Candidate not assigned to session rejected
7. Duplicate vote in same session rejected (engine and blockchain layer)
8. Same voter voting in another session accepted (cross-session blockchain isolation)
9. Blockchain integrity and tamper detection
10. Encryption/decryption verification using verify_vote_on_blockchain()
11. Multiple voters in one session (chain growth, tallies, and integrity)
"""

from pathlib import Path
import sys
import unittest

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from voting.voting_engine import (
    VotingEngine,
    VotingError,
    UnknownSessionError,
    InactiveSessionError,
    UnknownVoterError,
    IneligibleVoterError,
    UnknownCandidateError,
    CandidateNotAssignedError,
    DuplicateVoteError,
)


class TestVotingEngineM3(unittest.TestCase):
    """Unit test cases for M3 Quantum & Blockchain integrated VotingEngine."""

    def setUp(self):
        # Create fresh engine instance before each test
        self.engine = VotingEngine()

    def test_successful_end_to_end_vote(self):
        """Test 1: Successful end-to-end vote through full quantum and blockchain pipeline."""
        result = self.engine.cast_vote(
            session_id="SESS-003",
            voter_id="V001",
            candidate_id="C002",
        )

        # 1. Receipt verification
        self.assertTrue(result["success"])
        self.assertEqual(result["session_id"], "SESS-003")
        self.assertEqual(result["voter_id"], "V001")
        self.assertEqual(result["candidate_id"], "C002")
        self.assertIn("VOTE-", result["vote_id"])
        self.assertIsNotNone(result["timestamp"])
        self.assertIsNotNone(result["block_hash"])
        self.assertEqual(result["block_index"], 1)  # Index 1 after genesis (0)
        self.assertGreater(result["num_qubits"], 0)

        # 2. Blockchain state verification
        session_chain = self.engine.get_session_chain("SESS-003")
        self.assertEqual(len(session_chain.chain), 2)  # Genesis + 1 vote block
        self.assertTrue(session_chain.is_chain_valid())
        self.assertTrue(session_chain.has_voter_voted("V001"))

        # 3. Payload verification: ciphertext stored on chain is hex, not plaintext candidate
        block = session_chain.chain[1]
        stored_payload = block.vote_data["encrypted_vote"]
        self.assertNotIn("C002", stored_payload)
        self.assertEqual(bytes.fromhex(stored_payload).hex(), stored_payload)

    def test_unknown_voter_rejected(self):
        """Test 2: Unknown voter ID is rejected."""
        with self.assertRaises(UnknownVoterError):
            self.engine.cast_vote(
                session_id="SESS-003",
                voter_id="V999",
                candidate_id="C002",
            )

    def test_ineligible_voter_rejected(self):
        """Test 3: Known voter who is not enrolled in the session is rejected."""
        # V002 exists in voters.csv but is not enrolled in session_voters for SESS-003
        self.assertIn("V002", self.engine.voters)
        self.assertNotIn("V002", self.engine.session_voters["SESS-003"])
        with self.assertRaises(IneligibleVoterError):
            self.engine.cast_vote(
                session_id="SESS-003",
                voter_id="V002",
                candidate_id="C002",
            )

    def test_inactive_session_rejected(self):
        """Test 4: Sessions not in ACTIVE status (COMPLETED, UPCOMING) are rejected."""
        # SESS-001 is COMPLETED
        self.assertEqual(self.engine.sessions["SESS-001"]["status"], "COMPLETED")
        with self.assertRaises(InactiveSessionError):
            self.engine.cast_vote(
                session_id="SESS-001",
                voter_id="V001",
                candidate_id="C008",
            )

        # SESS-004 is UPCOMING
        self.assertEqual(self.engine.sessions["SESS-004"]["status"], "UPCOMING")
        with self.assertRaises(InactiveSessionError):
            self.engine.cast_vote(
                session_id="SESS-004",
                voter_id="V001",
                candidate_id="C001",
            )

    def test_invalid_candidate_rejected(self):
        """Test 5: Unknown candidate ID is rejected."""
        with self.assertRaises(UnknownCandidateError):
            self.engine.cast_vote(
                session_id="SESS-003",
                voter_id="V001",
                candidate_id="C999",
            )

    def test_candidate_not_assigned_to_session_rejected(self):
        """Test 6: Known candidate not assigned to the selected session is rejected."""
        # C001 is a valid candidate but not assigned to SESS-003
        self.assertIn("C001", self.engine.candidates)
        self.assertNotIn("C001", self.engine.session_candidates["SESS-003"])
        with self.assertRaises(CandidateNotAssignedError):
            self.engine.cast_vote(
                session_id="SESS-003",
                voter_id="V001",
                candidate_id="C001",
            )

    def test_duplicate_vote_in_same_session_rejected(self):
        """Test 7: Second vote by the same voter within the same session is rejected."""
        self.engine.cast_vote("SESS-003", "V001", "C002")
        self.assertEqual(self.engine.count_accepted_votes("SESS-003"), 1)

        # Attempt second vote in SESS-003
        with self.assertRaises(DuplicateVoteError):
            self.engine.cast_vote("SESS-003", "V001", "C011")

        # Blockchain should still have exactly 1 vote block (plus genesis)
        chain = self.engine.get_session_chain("SESS-003")
        self.assertEqual(chain.get_vote_count(), 1)

    def test_same_voter_in_another_session_accepted(self):
        """Test 8: The same voter is permitted to vote in multiple different sessions."""
        # 1. Vote in SESS-003 (ACTIVE)
        res1 = self.engine.cast_vote("SESS-003", "V001", "C002")
        self.assertTrue(res1["success"])

        # 2. Activate SESS-004 in memory on this engine instance without modifying CSV
        self.engine.set_session_status("SESS-004", "ACTIVE")
        self.assertIn("V001", self.engine.session_voters["SESS-004"])
        self.assertIn("C001", self.engine.session_candidates["SESS-004"])

        # 3. Vote in SESS-004
        res2 = self.engine.cast_vote("SESS-004", "V001", "C001")
        self.assertTrue(res2["success"])

        # Verification: voter has voted in both sessions, chains are independent
        chain3 = self.engine.get_session_chain("SESS-003")
        chain4 = self.engine.get_session_chain("SESS-004")
        self.assertEqual(chain3.get_vote_count(), 1)
        self.assertEqual(chain4.get_vote_count(), 1)
        self.assertTrue(chain3.is_chain_valid())
        self.assertTrue(chain4.is_chain_valid())

    def test_blockchain_integrity_and_tamper_detection(self):
        """Test 9: Blockchain integrity verification and tamper detection."""
        res = self.engine.cast_vote("SESS-003", "V001", "C002")
        chain = self.engine.get_session_chain("SESS-003")
        self.assertTrue(chain.is_chain_valid())

        # Simulate tampering with the encrypted payload in Block #1
        original_payload = chain.chain[1].vote_data["encrypted_vote"]
        chain.chain[1].vote_data["encrypted_vote"] = "ffff" * 16
        self.assertFalse(chain.is_chain_valid(), "Tampered payload must fail chain validation")

        # Restore original payload and confirm validity returns
        chain.chain[1].vote_data["encrypted_vote"] = original_payload
        self.assertTrue(chain.is_chain_valid())

    def test_encryption_decryption_verification(self):
        """Test 10: Verification method retrieves from blockchain, decrypts, and matches original."""
        res = self.engine.cast_vote("SESS-003", "V001", "C002")
        vote_id = res["vote_id"]

        # Call the dedicated M3 verification method
        verification = self.engine.verify_vote_on_blockchain(
            session_id="SESS-003",
            vote_id=vote_id,
        )

        self.assertTrue(verification["verified"])
        self.assertTrue(verification["matches_original"])
        self.assertEqual(verification["decrypted_candidate"], "C002")
        self.assertEqual(verification["original_candidate"], "C002")
        self.assertEqual(verification["block_index"], 1)

    def test_multiple_voters_in_one_session(self):
        """Test 11: Multiple distinct voters casting votes in one session."""
        # V001, V003, V004, V005 are all eligible for SESS-003
        voters_choices = [
            ("V001", "C002"),
            ("V003", "C002"),
            ("V004", "C007"),
            ("V005", "C011"),
        ]

        receipts = []
        for vid, cid in voters_choices:
            receipt = self.engine.cast_vote("SESS-003", vid, cid)
            receipts.append(receipt)

        chain = self.engine.get_session_chain("SESS-003")
        self.assertEqual(chain.get_vote_count(), 4)
        self.assertEqual(len(chain.chain), 5)  # Genesis + 4 blocks
        self.assertTrue(chain.is_chain_valid())

        # Verify hash link chain: each block's prev_hash must equal predecessor's hash
        for i in range(1, len(chain.chain)):
            self.assertEqual(chain.chain[i].prev_hash, chain.chain[i - 1].hash)

        # Verify each vote can be decrypted and verified from the blockchain
        for receipt in receipts:
            v_check = self.engine.verify_vote_on_blockchain("SESS-003", receipt["vote_id"])
            self.assertTrue(v_check["verified"])
            self.assertEqual(v_check["decrypted_candidate"], receipt["candidate_id"])

        # Check tallies
        tally = self.engine.get_tally("SESS-003")
        self.assertEqual(tally["C002"], 2)
        self.assertEqual(tally["C007"], 1)
        self.assertEqual(tally["C011"], 1)


def run_all_tests():
    """Console test runner matching repository's prototype test style."""
    print("=" * 60)
    print("M3: INTEGRATED QUANTUM VOTING ENGINE TEST SUITE")
    print("=" * 60)

    engine = VotingEngine()

    # ── Test 1: Successful end-to-end vote ──
    print("\n=== Test 1: Successful end-to-end quantum + blockchain vote ===")
    res = engine.cast_vote("SESS-003", "V001", "C002")
    assert res["success"] is True
    assert res["block_index"] == 1
    assert "block_hash" in res and len(res["block_hash"]) == 64
    chain = engine.get_session_chain("SESS-003")
    assert chain.get_vote_count() == 1
    assert chain.is_chain_valid() is True
    print(f"  PASSED: Vote recorded (vote_id={res['vote_id']}, block={res['block_index']}, hash={res['block_hash'][:16]}...)")

    # ── Test 2: Unknown voter rejected ──
    print("\n=== Test 2: Unknown voter rejected ===")
    try:
        engine.cast_vote("SESS-003", "V999", "C002")
        assert False, "Should have raised UnknownVoterError"
    except UnknownVoterError as ex:
        print(f"  PASSED: Correctly rejected ({ex})")

    # ── Test 3: Ineligible voter rejected ──
    print("\n=== Test 3: Ineligible voter rejected ===")
    try:
        engine.cast_vote("SESS-003", "V002", "C002")
        assert False, "Should have raised IneligibleVoterError"
    except IneligibleVoterError as ex:
        print(f"  PASSED: Correctly rejected ({ex})")

    # ── Test 4: Inactive session rejected ──
    print("\n=== Test 4: Inactive session rejected ===")
    try:
        engine.cast_vote("SESS-001", "V001", "C008")
        assert False, "Should have raised InactiveSessionError for COMPLETED session"
    except InactiveSessionError as ex:
        print(f"  PASSED: COMPLETED session rejected ({ex})")

    try:
        engine.cast_vote("SESS-004", "V001", "C001")
        assert False, "Should have raised InactiveSessionError for UPCOMING session"
    except InactiveSessionError as ex:
        print(f"  PASSED: UPCOMING session rejected ({ex})")

    # ── Test 5: Invalid candidate rejected ──
    print("\n=== Test 5: Invalid candidate rejected ===")
    try:
        engine.cast_vote("SESS-003", "V003", "C999")
        assert False, "Should have raised UnknownCandidateError"
    except UnknownCandidateError as ex:
        print(f"  PASSED: Correctly rejected ({ex})")

    # ── Test 6: Candidate not assigned to session rejected ──
    print("\n=== Test 6: Candidate not assigned to session rejected ===")
    try:
        engine.cast_vote("SESS-003", "V003", "C001")
        assert False, "Should have raised CandidateNotAssignedError"
    except CandidateNotAssignedError as ex:
        print(f"  PASSED: Correctly rejected ({ex})")

    # ── Test 7: Duplicate vote in same session rejected ──
    print("\n=== Test 7: Duplicate vote in same session rejected ===")
    try:
        engine.cast_vote("SESS-003", "V001", "C011")
        assert False, "Should have raised DuplicateVoteError"
    except DuplicateVoteError as ex:
        print(f"  PASSED: Correctly rejected duplicate vote ({ex})")

    # ── Test 8: Same voter voting in another session accepted ──
    print("\n=== Test 8: Same voter voting in another session accepted ===")
    engine.set_session_status("SESS-004", "ACTIVE")
    res_sess4 = engine.cast_vote("SESS-004", "V001", "C001")
    assert res_sess4["success"] is True
    chain4 = engine.get_session_chain("SESS-004")
    assert chain4.get_vote_count() == 1
    assert chain4.is_chain_valid() is True
    print(f"  PASSED: V001 successfully voted in SESS-003 and SESS-004 (independent blockchains)")

    # ── Test 9: Blockchain integrity & tamper detection ──
    print("\n=== Test 9: Blockchain integrity and tamper detection ===")
    chain3 = engine.get_session_chain("SESS-003")
    assert chain3.is_chain_valid() is True
    orig_payload = chain3.chain[1].vote_data["encrypted_vote"]
    chain3.chain[1].vote_data["encrypted_vote"] = "badpayload" * 4
    assert chain3.is_chain_valid() is False, "Tamper detection failed"
    chain3.chain[1].vote_data["encrypted_vote"] = orig_payload
    assert chain3.is_chain_valid() is True
    print("  PASSED: Chain integrity check & tamper detection confirmed")

    # ── Test 10: Decryption & verification from blockchain ──
    print("\n=== Test 10: Blockchain vote retrieval, decryption & verification ===")
    ver = engine.verify_vote_on_blockchain("SESS-003", res["vote_id"])
    assert ver["verified"] is True
    assert ver["decrypted_candidate"] == "C002"
    print(f"  PASSED: Successfully decrypted payload from block #{ver['block_index']} -> Candidate: {ver['decrypted_candidate']}")

    # ── Test 11: Multiple voters in one session ──
    print("\n=== Test 11: Multiple voters in one session ===")
    # Add V003 and V004 votes to SESS-003
    r3 = engine.cast_vote("SESS-003", "V003", "C002")
    r4 = engine.cast_vote("SESS-003", "V004", "C007")
    assert chain3.get_vote_count() == 3
    assert chain3.is_chain_valid() is True
    tally = engine.get_tally("SESS-003")
    assert tally["C002"] == 2
    assert tally["C007"] == 1
    assert tally["C011"] == 0
    print(f"  PASSED: 3 votes recorded in SESS-003. Tally: {tally}")

    print("\n" + "=" * 60)
    print("  ALL M3 QUANTUM VOTING ENGINE TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    run_all_tests()
