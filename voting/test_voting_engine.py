"""
test_voting_engine.py
Comprehensive test suite for Multi-Voter Voting Engine supporting the corrected M1 data model.

Covers:
1. Successful votes across all three session types:
   - candidate_election (people candidates only, SESS-004)
   - yes_no (Yes/No required, optional Abstain, SESS-002)
   - single_choice (decision alternatives, SESS-008)
2. Strict choice argument validation:
   - Exactly one of choice_id, candidate_id, or option_id permitted.
   - Rejecting 0 or multiple choice arguments.
3. Choice type and assignment validation:
   - Rejecting option IDs in candidate_election sessions.
   - Rejecting candidate IDs in decision sessions.
   - Rejecting unknown candidates/options.
   - Rejecting unassigned candidates/options (e.g. Abstain in SESS-003).
4. Voter existence and eligibility enforcement:
   - Ineligible voter rejected using smaller-subset session SESS-003 activated in fixture.
5. Inactive and out-of-window session rejection:
   - COMPLETED sessions, UPCOMING sessions, and expired active windows.
6. Duplicate vote prevention & Concurrency:
   - Sequential duplicate vote rejected.
   - Simultaneous multithreaded submissions by the same voter produce exactly one accepted vote.
7. Multi-session participation:
   - Same voter permitted to vote in distinct eligible sessions with independent blockchains.
8. Privacy-preserving receipts and verification:
   - Receipts do NOT leak plaintext choices.
   - Verification accepts explicit expected_choice input without retaining plaintext links.
9. Security gates:
   - BB84 failure stops pipeline before encryption or ledger write.
   - Reservation released on BB84 failure, enabling retry.
10. Tamper detection and blockchain ledger integrity.
11. CSV data validation and duplicate detection.
"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from voting.ballot_encoding import encode_choice
from voting.voting_engine import (
    VotingEngine,
    VotingError,
    UnknownSessionError,
    InactiveSessionError,
    UnknownVoterError,
    IneligibleVoterError,
    UnknownCandidateError,
    CandidateNotAssignedError,
    InvalidChoiceError,
    UnknownOptionError,
    OptionNotAssignedError,
    DuplicateVoteError,
    BB84SecurityError,
    get_session_choices,
)


class TestVotingEngineM3(unittest.TestCase):
    """Unit test cases for M1-supported Multi-Voter VotingEngine."""

    def setUp(self):
        # Create fresh engine instance before each test
        self.engine = VotingEngine(use_postgres=False)
        # Activate SESS-004 with a valid current voting window so tests
        # never depend on the (possibly stale) CSV timestamps.
        self.engine.set_session_status("SESS-004", "ACTIVE")

    def test_successful_end_to_end_vote_candidate_election(self):
        """Test 1: Successful end-to-end vote in candidate_election session (SESS-004, ACTIVE)."""
        result = self.engine.cast_vote(
            session_id="SESS-004",
            voter_id="V001",
            candidate_id="C002",
        )

        # 1. Receipt verification (does NOT leak plaintext choice)
        self.assertTrue(result["success"])
        self.assertEqual(result["session_id"], "SESS-004")
        self.assertEqual(result["voter_id"], "V001")
        self.assertNotIn("choice_id", result)
        self.assertNotIn("candidate_id", result)
        self.assertNotIn("option_id", result)
        self.assertIn("VOTE-", result["vote_id"])
        self.assertIsNotNone(result["timestamp"])
        self.assertIsNotNone(result["block_hash"])
        self.assertEqual(result["block_index"], 1)  # Index 1 after genesis (0)
        self.assertGreater(result["num_qubits"], 0)

        # 2. Blockchain state verification
        session_chain = self.engine.get_session_chain("SESS-004")
        self.assertEqual(len(session_chain.chain), 2)  # Genesis + 1 vote block
        self.assertTrue(session_chain.is_chain_valid())
        self.assertTrue(session_chain.has_voter_voted("V001"))

        # 3. Payload verification: ciphertext stored on chain is hex, not plaintext candidate
        block = session_chain.chain[1]
        stored_payload = block.vote_data["encrypted_vote"]
        self.assertNotIn("C002", stored_payload)
        self.assertEqual(bytes.fromhex(stored_payload).hex(), stored_payload)

        # 4. Roundtrip decryption & verification using explicit expected_choice
        ver = self.engine.verify_vote_on_blockchain(
            session_id="SESS-004",
            vote_id=result["vote_id"],
            expected_choice="C002",
        )
        self.assertTrue(ver["verified"])
        self.assertTrue(ver["matches_original"])
        self.assertEqual(ver["decrypted_choice"], "C002")

    def test_successful_end_to_end_vote_yes_no(self):
        """Test 2: Successful end-to-end votes in yes_no session (SESS-002: Yes, No, Abstain)."""
        self.engine.set_session_status("SESS-002", "ACTIVE")
        s2_voters = sorted(list(self.engine.session_voters["SESS-002"]))

        # Vote Yes (O001)
        r_yes = self.engine.cast_vote(session_id="SESS-002", voter_id=s2_voters[0], option_id="O001")
        self.assertTrue(r_yes["success"])
        self.assertNotIn("choice_id", r_yes)

        # Vote No (O002)
        r_no = self.engine.cast_vote(session_id="SESS-002", voter_id=s2_voters[1], choice_id="O002")
        self.assertTrue(r_no["success"])

        # Vote Abstain (O003) - optional choice
        r_abs = self.engine.cast_vote(session_id="SESS-002", voter_id=s2_voters[2], option_id="O003")
        self.assertTrue(r_abs["success"])

        # Blockchain & Tally verification
        chain = self.engine.get_session_chain("SESS-002")
        self.assertEqual(chain.get_vote_count(), 3)
        self.assertTrue(chain.is_chain_valid())

        tally = self.engine.get_tally("SESS-002")
        self.assertEqual(tally["O001"], 1)
        self.assertEqual(tally["O002"], 1)
        self.assertEqual(tally["O003"], 1)

        # Verification of roundtrip decryption with explicit expected choice
        ver_abs = self.engine.verify_vote_on_blockchain("SESS-002", r_abs["vote_id"], expected_choice="O003")
        self.assertTrue(ver_abs["verified"])
        self.assertEqual(ver_abs["decrypted_choice"], "O003")

    def test_successful_end_to_end_vote_single_choice(self):
        """Test 3: Successful end-to-end vote in single_choice policy session (SESS-008: O016-O018)."""
        self.engine.set_session_status("SESS-008", "ACTIVE")
        s8_voters = sorted(list(self.engine.session_voters["SESS-008"]))

        result = self.engine.cast_vote(
            session_id="SESS-008",
            voter_id=s8_voters[0],
            option_id="O016",
        )
        self.assertTrue(result["success"])
        self.assertNotIn("choice_id", result)

        tally = self.engine.get_tally("SESS-008")
        self.assertEqual(tally["O016"], 1)
        self.assertEqual(tally["O017"], 0)
        self.assertEqual(tally["O018"], 0)

        ver = self.engine.verify_vote_on_blockchain("SESS-008", result["vote_id"], expected_choice="O016")
        self.assertTrue(ver["verified"])
        self.assertEqual(ver["decrypted_choice"], "O016")

    def test_strict_choice_arguments_required(self):
        """Test 4: Require exactly one choice argument; reject none or multiple values."""
        # 4.1 Supplying no choice argument
        with self.assertRaises(VotingError) as ctx_none:
            self.engine.cast_vote(
                session_id="SESS-004",
                voter_id="V001",
            )
        self.assertIn("No vote choice supplied", str(ctx_none.exception))

        # 4.2 Supplying both candidate_id and option_id
        with self.assertRaises(VotingError) as ctx_mult:
            self.engine.cast_vote(
                session_id="SESS-004",
                voter_id="V001",
                candidate_id="C002",
                option_id="O001",
            )
        self.assertIn("Multiple vote choices supplied", str(ctx_mult.exception))

        # 4.3 Supplying choice_id and candidate_id
        with self.assertRaises(VotingError):
            self.engine.cast_vote(
                session_id="SESS-004",
                voter_id="V001",
                choice_id="C002",
                candidate_id="C002",
            )

    def test_unknown_voter_rejected(self):
        """Test 5: Unknown voter ID is rejected."""
        with self.assertRaises(UnknownVoterError):
            self.engine.cast_vote(
                session_id="SESS-004",
                voter_id="V999",
                candidate_id="C002",
            )

    def test_ineligible_voter_rejected(self):
        """Test 6: Known voter not enrolled in a smaller-subset session is rejected (SESS-003 made active in fixture)."""
        # SESS-004 has all 400 voters, so it cannot test ineligibility.
        # SESS-003 has 200 voters; activate it in memory and test an unenrolled voter.
        self.engine.set_session_status("SESS-003", "ACTIVE")
        all_voter_ids = set(self.engine.voters.keys())
        s3_voters = self.engine.session_voters["SESS-003"]
        ineligible_candidates = sorted(list(all_voter_ids - s3_voters))
        self.assertGreater(len(ineligible_candidates), 0)

        ineligible_vid = ineligible_candidates[0]
        self.assertIn(ineligible_vid, self.engine.voters)
        self.assertNotIn(ineligible_vid, self.engine.session_voters["SESS-003"])

        with self.assertRaises(IneligibleVoterError):
            self.engine.cast_vote(
                session_id="SESS-003",
                voter_id=ineligible_vid,
                option_id="O001",
            )

    def test_inactive_and_out_of_window_session_rejected(self):
        """Test 7: Sessions not in ACTIVE status or outside their time window are rejected."""
        # 7.1 SESS-001 is COMPLETED
        self.assertEqual(self.engine.sessions["SESS-001"]["status"], "COMPLETED")
        with self.assertRaises(InactiveSessionError):
            self.engine.cast_vote(
                session_id="SESS-001",
                voter_id="V001",
                candidate_id="C001",
            )

        # 7.2 SESS-005 is UPCOMING
        self.assertEqual(self.engine.sessions["SESS-005"]["status"], "UPCOMING")
        with self.assertRaises(InactiveSessionError):
            self.engine.cast_vote(
                session_id="SESS-005",
                voter_id="V001",
                option_id="O004",
            )

        # 7.3 ACTIVE session with expired end_time is rejected
        self.engine.sessions["SESS-004"]["end_time"] = "2020-01-01T00:00:00Z"
        with self.assertRaises(InactiveSessionError):
            self.engine.cast_vote(
                session_id="SESS-004",
                voter_id="V001",
                candidate_id="C002",
            )

    def test_unknown_and_unassigned_candidate_rejected(self):
        """Test 8: Unknown and unassigned candidates in candidate_election are rejected."""
        # Unknown candidate C999
        with self.assertRaises(UnknownCandidateError):
            self.engine.cast_vote(
                session_id="SESS-004",
                voter_id="V001",
                candidate_id="C999",
            )

        # Candidate temporarily removed from session_candidates
        self.engine.session_candidates["SESS-004"].remove("C015")
        with self.assertRaises(CandidateNotAssignedError):
            self.engine.cast_vote(
                session_id="SESS-004",
                voter_id="V001",
                candidate_id="C015",
            )

    def test_unknown_and_unassigned_option_rejected(self):
        """Test 9: Unknown and unassigned options in decision sessions are rejected."""
        self.engine.set_session_status("SESS-002", "ACTIVE")
        s2_voter = sorted(list(self.engine.session_voters["SESS-002"]))[0]

        # Unknown option O999
        with self.assertRaises(UnknownOptionError):
            self.engine.cast_vote(
                session_id="SESS-002",
                voter_id=s2_voter,
                option_id="O999",
            )

        # Unassigned option: O010 (Provider North) not in SESS-002
        with self.assertRaises(OptionNotAssignedError):
            self.engine.cast_vote(
                session_id="SESS-002",
                voter_id=s2_voter,
                option_id="O010",
            )

        # In SESS-003, Abstain (O003) is intentionally NOT assigned
        self.engine.set_session_status("SESS-003", "ACTIVE")
        s3_voter = sorted(list(self.engine.session_voters["SESS-003"]))[0]
        with self.assertRaises(OptionNotAssignedError):
            self.engine.cast_vote(
                session_id="SESS-003",
                voter_id=s3_voter,
                option_id="O003",
            )

    def test_wrong_type_choice_rejected(self):
        """Test 10: Reject option ID in candidate_election and candidate ID in decision session."""
        # 10.1 Option in candidate election
        with self.assertRaises(InvalidChoiceError):
            self.engine.cast_vote(
                session_id="SESS-004",
                voter_id="V001",
                choice_id="O001",
            )

        with self.assertRaises(InvalidChoiceError):
            self.engine.cast_vote(
                session_id="SESS-004",
                voter_id="V001",
                option_id="O001",
            )

        # 10.2 Candidate in yes_no session
        self.engine.set_session_status("SESS-002", "ACTIVE")
        s2_voter = sorted(list(self.engine.session_voters["SESS-002"]))[0]
        with self.assertRaises(InvalidChoiceError):
            self.engine.cast_vote(
                session_id="SESS-002",
                voter_id=s2_voter,
                choice_id="C001",
            )

        with self.assertRaises(InvalidChoiceError):
            self.engine.cast_vote(
                session_id="SESS-002",
                voter_id=s2_voter,
                candidate_id="C001",
            )

        # 10.3 Candidate in single_choice session
        self.engine.set_session_status("SESS-008", "ACTIVE")
        s8_voter = sorted(list(self.engine.session_voters["SESS-008"]))[0]
        with self.assertRaises(InvalidChoiceError):
            self.engine.cast_vote(
                session_id="SESS-008",
                voter_id=s8_voter,
                choice_id="C001",
            )

    def test_duplicate_vote_in_same_session_rejected(self):
        """Test 11: Second vote by the same voter within the same session is rejected."""
        self.engine.cast_vote("SESS-004", "V001", candidate_id="C002")
        self.assertEqual(self.engine.count_accepted_votes("SESS-004"), 1)

        # Attempt second vote in SESS-004
        with self.assertRaises(DuplicateVoteError):
            self.engine.cast_vote("SESS-004", "V001", candidate_id="C011")

        # Blockchain should still have exactly 1 vote block (plus genesis)
        chain = self.engine.get_session_chain("SESS-004")
        self.assertEqual(chain.get_vote_count(), 1)

    def test_concurrent_submissions_same_voter_produce_exactly_one_vote(self):
        """Test 12: Atomic reservation prevents duplicate votes under concurrent execution."""
        # 10 simultaneous threads attempting to cast a vote for V001 in SESS-004
        results = []
        errors = []

        def submit_vote(worker_idx):
            try:
                res = self.engine.cast_vote(
                    session_id="SESS-004",
                    voter_id="V001",
                    candidate_id="C002",
                )
                results.append((worker_idx, res))
            except Exception as e:
                errors.append((worker_idx, type(e), e))

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(submit_vote, i) for i in range(10)]
            for f in futures:
                f.result()

        # Exactly 1 vote must succeed; the other 9 must be rejected as DuplicateVoteError
        self.assertEqual(len(results), 1, f"Expected 1 successful vote, got {len(results)}")
        self.assertEqual(len(errors), 9, f"Expected 9 rejections, got {len(errors)}")
        for worker_idx, err_type, err in errors:
            self.assertEqual(err_type, DuplicateVoteError, f"Worker {worker_idx} raised unexpected error: {err}")

        # Blockchain must contain exactly 1 vote block (plus genesis)
        chain = self.engine.get_session_chain("SESS-004")
        self.assertEqual(chain.get_vote_count(), 1)
        self.assertEqual(self.engine.get_tally("SESS-004")["C002"], 1)

    def test_same_voter_in_another_session_accepted(self):
        """Test 13: The same voter is permitted to vote in multiple different sessions."""
        # 1. Vote in SESS-004 (ACTIVE candidate_election)
        res1 = self.engine.cast_vote("SESS-004", "V001", candidate_id="C002")
        self.assertTrue(res1["success"])

        # 2. Activate SESS-002 (yes_no) and check V001 or find a voter eligible for both
        self.engine.set_session_status("SESS-002", "ACTIVE")
        if "V001" not in self.engine.session_voters["SESS-002"]:
            self.engine.session_voters["SESS-002"].add("V001")

        # 3. Vote in SESS-002
        res2 = self.engine.cast_vote("SESS-002", "V001", option_id="O001")
        self.assertTrue(res2["success"])

        # Verification: independent chains
        chain4 = self.engine.get_session_chain("SESS-004")
        chain2 = self.engine.get_session_chain("SESS-002")
        self.assertEqual(chain4.get_vote_count(), 1)
        self.assertEqual(chain2.get_vote_count(), 1)
        self.assertTrue(chain4.is_chain_valid())
        self.assertTrue(chain2.is_chain_valid())

    def test_blockchain_integrity_and_tamper_detection(self):
        """Test 14: Blockchain integrity verification and tamper detection."""
        res = self.engine.cast_vote("SESS-004", "V001", candidate_id="C002")
        chain = self.engine.get_session_chain("SESS-004")
        self.assertTrue(chain.is_chain_valid())

        # Simulate tampering with the encrypted payload in Block #1
        original_payload = chain.chain[1].vote_data["encrypted_vote"]
        chain.chain[1].vote_data["encrypted_vote"] = "ffff" * 16
        self.assertFalse(chain.is_chain_valid(), "Tampered payload must fail chain validation")

        # Restore original payload and confirm validity returns
        chain.chain[1].vote_data["encrypted_vote"] = original_payload
        self.assertTrue(chain.is_chain_valid())

    def test_multiple_voters_and_tallies(self):
        """Test 15: Multiple distinct voters in candidate_election and decision sessions with clean tallies."""
        # 15.1 Multi-voter in SESS-004
        voters_choices = [
            ("V001", "C002"),
            ("V003", "C002"),
            ("V004", "C007"),
            ("V005", "C011"),
        ]
        for vid, cid in voters_choices:
            self.engine.cast_vote("SESS-004", vid, candidate_id=cid)

        chain4 = self.engine.get_session_chain("SESS-004")
        self.assertEqual(chain4.get_vote_count(), 4)
        self.assertTrue(chain4.is_chain_valid())

        tally4 = self.engine.get_tally("SESS-004")
        self.assertEqual(tally4["C002"], 2)
        self.assertEqual(tally4["C007"], 1)
        self.assertEqual(tally4["C011"], 1)
        self.assertNotIn("O001", tally4)

        # 15.2 Multi-voter in SESS-002 (decision session)
        self.engine.set_session_status("SESS-002", "ACTIVE")
        s2_voters = sorted(list(self.engine.session_voters["SESS-002"]))
        self.engine.cast_vote("SESS-002", s2_voters[0], option_id="O001")
        self.engine.cast_vote("SESS-002", s2_voters[1], option_id="O001")
        self.engine.cast_vote("SESS-002", s2_voters[2], option_id="O003")

        tally2 = self.engine.get_tally("SESS-002")
        self.assertEqual(tally2["O001"], 2)
        self.assertEqual(tally2["O002"], 0)
        self.assertEqual(tally2["O003"], 1)
        self.assertNotIn("C002", tally2)

    @patch("voting.voting_engine.encrypt_vote")
    @patch("voting.voting_engine.run_secure_bb84")
    def test_bb84_failure_prevents_encryption_and_releases_reservation(self, mock_run_bb84, mock_encrypt):
        """Test 16: Insecure BB84 halts pipeline before encryption/ledger AND releases voter reservation."""
        mock_run_bb84.return_value = {
            "secure": False,
            "aborted": True,
            "qber": 0.28,
            "sample_size": 16,
            "error_count": 5,
            "qber_threshold": 0.11,
            "sifted_key_length": 32,
            "final_key": None,
            "eavesdrop": True,
            "reason": "qber_threshold_exceeded",
        }

        chain = self.engine.get_session_chain("SESS-004")
        initial_block_count = len(chain.chain)  # 1 (genesis)

        with self.assertRaises(BB84SecurityError) as ctx:
            self.engine.cast_vote(
                session_id="SESS-004",
                voter_id="V001",
                candidate_id="C002",
            )

        self.assertIn("BB84 security check failed", str(ctx.exception))
        mock_encrypt.assert_not_called()
        self.assertEqual(len(chain.chain), initial_block_count)
        self.assertEqual(chain.get_vote_count(), 0)
        self.assertFalse(chain.has_voter_voted("V001"))

        # Reservation must be released so V001 can retry
        self.assertNotIn(("SESS-004", "V001"), self.engine._reserved_voters)
        self.assertNotIn(("SESS-004", "V001"), self.engine._voted_voters)

        # Restore normal BB84 and verify retry succeeds
        mock_run_bb84.return_value = {
            "secure": True,
            "aborted": False,
            "final_key": [1, 0] * 128,
        }
        mock_encrypt.return_value = b"test_ciphertext"
        retry_res = self.engine.cast_vote("SESS-004", "V001", candidate_id="C002")
        self.assertTrue(retry_res["success"])
        self.assertEqual(chain.get_vote_count(), 1)

    @patch("voting.voting_engine.encrypt_vote")
    @patch("voting.voting_engine.run_secure_bb84")
    def test_bb84_undersized_key_fails_closed_and_releases_reservation(self, mock_run_bb84, mock_encrypt):
        """M4/M2: BB84 returning undersized key (<256 bits) fails closed and releases reservation."""
        mock_run_bb84.return_value = {
            "secure": True,
            "aborted": False,
            "final_key": [1, 0] * 64,  # Only 128 bits
        }

        chain = self.engine.get_session_chain("SESS-004")
        initial_block_count = len(chain.chain)

        with self.assertRaises(BB84SecurityError) as ctx:
            self.engine.cast_vote(
                session_id="SESS-004",
                voter_id="V001",
                candidate_id="C002",
            )

        self.assertIn("undersized or invalid key", str(ctx.exception))
        mock_encrypt.assert_not_called()
        self.assertEqual(len(chain.chain), initial_block_count)
        self.assertNotIn(("SESS-004", "V001"), self.engine._reserved_voters)

    def test_data_validation_rules(self):
        """Test 17: validate_data() enforces schema rules for session types."""
        # 17.1 candidate_election with options attached
        self.engine.session_options["SESS-004"].add("O001")
        with self.assertRaises(VotingError):
            self.engine.validate_data()
        self.engine.session_options["SESS-004"].remove("O001")

        # 17.2 yes_no session with candidate attached
        self.engine.session_candidates["SESS-002"].add("C001")
        with self.assertRaises(VotingError):
            self.engine.validate_data()
        self.engine.session_candidates["SESS-002"].remove("C001")

        # 17.3 single_choice with yes_no option attached
        self.engine.session_options["SESS-008"].add("O001")
        with self.assertRaises(VotingError):
            self.engine.validate_data()
        self.engine.session_options["SESS-008"].remove("O001")

    def test_verification_rejects_wrong_session(self):
        """Test 18: verify_vote_on_blockchain rejects if supplied session_id does not match recorded session."""
        res = self.engine.cast_vote(session_id="SESS-004", voter_id="V001", candidate_id="C002")
        vote_id = res["vote_id"]

        with self.assertRaises(VotingError) as ctx:
            self.engine.verify_vote_on_blockchain(
                session_id="SESS-002",
                vote_id=vote_id,
                expected_choice="C002",
            )
        self.assertIn("was recorded in session 'SESS-004'", str(ctx.exception))

    def test_verification_rejects_wrong_block_and_disallows_bypass(self):
        """Test 19: verify_vote_on_blockchain rejects mismatched block_index and disallows bypass."""
        res = self.engine.cast_vote(session_id="SESS-004", voter_id="V001", candidate_id="C002")
        vote_id = res["vote_id"]
        actual_block = res["block_index"]

        # 19.1 Supplying wrong block_index raises VotingError
        with self.assertRaises(VotingError) as ctx:
            self.engine.verify_vote_on_blockchain(
                session_id="SESS-004",
                vote_id=vote_id,
                expected_choice="C002",
                block_index=actual_block + 10,
            )
        self.assertIn("does not match recorded block_index", str(ctx.exception))

        # 19.2 Supplying the correct block_index succeeds
        ver = self.engine.verify_vote_on_blockchain(
            session_id="SESS-004",
            vote_id=vote_id,
            expected_choice="C002",
            block_index=actual_block,
        )
        self.assertTrue(ver["verified"])

    def test_verification_explicit_semantics(self):
        """Test 20: Explicit verification semantics when expected_choice is omitted vs provided."""
        res = self.engine.cast_vote(session_id="SESS-004", voter_id="V001", candidate_id="C002")
        vote_id = res["vote_id"]

        # 20.1 expected_choice omitted -> decryptable=True, verified=False
        ver_omitted = self.engine.verify_vote_on_blockchain(
            session_id="SESS-004",
            vote_id=vote_id,
        )
        self.assertTrue(ver_omitted["decryptable"])
        self.assertFalse(ver_omitted["verified"])
        self.assertFalse(ver_omitted["matches_original"])
        self.assertIsNone(ver_omitted["expected_choice"])
        self.assertEqual(ver_omitted["decrypted_choice"], "C002")

        # 20.2 require_expected_choice=True and expected_choice omitted -> raises VotingError
        with self.assertRaises(VotingError) as ctx:
            self.engine.verify_vote_on_blockchain(
                session_id="SESS-004",
                vote_id=vote_id,
                require_expected_choice=True,
            )
        self.assertIn("expected_choice is required", str(ctx.exception))

        # 20.3 expected_choice provided and matches -> decryptable=True, verified=True
        ver_match = self.engine.verify_vote_on_blockchain(
            session_id="SESS-004",
            vote_id=vote_id,
            expected_choice="C002",
        )
        self.assertTrue(ver_match["decryptable"])
        self.assertTrue(ver_match["verified"])
        self.assertEqual(ver_match["expected_choice"], "C002")

        # 20.4 expected_choice provided and mismatches -> decryptable=True, verified=False
        ver_mismatch = self.engine.verify_vote_on_blockchain(
            session_id="SESS-004",
            vote_id=vote_id,
            expected_choice="C001",
        )
        self.assertTrue(ver_mismatch["decryptable"])
        self.assertFalse(ver_mismatch["verified"])

    def test_commit_failure_rollback_maintains_consistent_state(self):
        """Test 21: Failure during commit metadata update rolls back appended block atomically."""
        chain = self.engine.get_session_chain("SESS-004")
        self.assertEqual(len(chain.chain), 1)  # Genesis only

        with patch.object(self.engine, "_commit_vote_metadata", side_effect=RuntimeError("Simulated commit failure")):
            with self.assertRaises(RuntimeError):
                self.engine.cast_vote(session_id="SESS-004", voter_id="V001", candidate_id="C001")

        # Invariant checks after commit failure:
        # 1. No orphan block on chain: chain length is still 1
        self.assertEqual(len(chain.chain), 1)
        self.assertEqual(chain.get_vote_count(), 0)
        self.assertFalse(chain.has_voter_voted("V001"))

        # 2. Engine state reports voter has NOT voted
        self.assertFalse(self.engine.has_voter_voted("SESS-004", "V001"))
        self.assertEqual(self.engine.count_accepted_votes("SESS-004"), 0)

        # 3. Reservation was discarded, allowing clean retry
        self.assertNotIn(("SESS-004", "V001"), self.engine._reserved_voters)
        self.assertNotIn(("SESS-004", "V001"), self.engine._voted_voters)

        # 4. Clean retry succeeds and appends block 1
        retry_res = self.engine.cast_vote(session_id="SESS-004", voter_id="V001", candidate_id="C001")
        self.assertTrue(retry_res["success"])
        self.assertEqual(retry_res["block_index"], 1)
        self.assertEqual(len(chain.chain), 2)
        self.assertEqual(self.engine.count_accepted_votes("SESS-004"), 1)

    def test_partial_commit_failure_rolls_back_all_metadata_and_blockchain(self):
        """Test 24: Partial mutation during commit metadata update rolls back all state atomically."""
        chain = self.engine.get_session_chain("SESS-004")

        # Establish existing baseline state with one committed vote
        base_res = self.engine.cast_vote(session_id="SESS-004", voter_id="V001", candidate_id="C001")
        self.assertTrue(base_res["success"])
        self.assertEqual(len(chain.chain), 2)  # Genesis + 1

        # Capture pre-failure snapshot of all listed in-memory items and blockchain
        pre_chain_len = len(chain.chain)
        pre_keystore = {k: list(v) for k, v in self.engine._prototype_keystore.items()}
        pre_metadata = {k: dict(v) for k, v in self.engine._prototype_vote_metadata.items()}
        pre_blocks = dict(self.engine._prototype_vote_blocks)
        pre_tallies = {sid: dict(c) for sid, c in self.engine._tallies.items()}
        pre_total_votes = dict(self.engine._total_votes)
        pre_vote_counter = self.engine._vote_counter
        pre_voted_voters = set(self.engine._voted_voters)
        pre_reserved_voters = set(self.engine._reserved_voters)

        # Failure injection: partially mutate keystore, metadata, tallies, and total_votes, then raise
        def partial_commit_and_fail(vote_id, session_id, voter_id, block_index, bb84_key, choice):
            self.engine._prototype_keystore[vote_id] = bb84_key
            self.engine._prototype_vote_metadata[vote_id] = {
                "session_id": session_id,
                "block_index": block_index,
                "voter_id": voter_id,
            }
            self.engine._prototype_vote_blocks[vote_id] = block_index
            self.engine._tallies[session_id][choice] = self.engine._tallies[session_id].get(choice, 0) + 1
            self.engine._total_votes[session_id] = self.engine._total_votes.get(session_id, 0) + 1
            raise RuntimeError("Simulated crash after partial in-memory metadata update")

        with patch.object(self.engine, "_commit_vote_metadata", side_effect=partial_commit_and_fail):
            with self.assertRaises(RuntimeError):
                self.engine.cast_vote(session_id="SESS-004", voter_id="V002", candidate_id="C002")

        # 1. Assert blockchain has no extra block and remains cryptographically valid
        self.assertEqual(len(chain.chain), pre_chain_len)
        self.assertEqual(chain.get_vote_count(), 1)
        self.assertTrue(chain.is_chain_valid())
        self.assertFalse(chain.has_voter_voted("V002"))

        # 2. Assert all listed in-memory state is restored to its exact pre-vote state
        self.assertEqual(self.engine._prototype_keystore, pre_keystore)
        self.assertEqual(self.engine._prototype_vote_metadata, pre_metadata)
        self.assertEqual(self.engine._prototype_vote_blocks, pre_blocks)
        self.assertEqual(self.engine._tallies, pre_tallies)
        self.assertEqual(self.engine._total_votes, pre_total_votes)
        self.assertEqual(self.engine._vote_counter, pre_vote_counter)
        self.assertEqual(self.engine._voted_voters, pre_voted_voters)
        self.assertEqual(self.engine._reserved_voters, pre_reserved_voters)

        # 3. Assert voter is not considered to have voted
        self.assertFalse(self.engine.has_voter_voted("SESS-004", "V002"))
        self.assertNotIn(("SESS-004", "V002"), self.engine._voted_voters)

        # 4. Assert reservation is cleared
        self.assertNotIn(("SESS-004", "V002"), self.engine._reserved_voters)

        # 5. Assert voter can retry successfully afterward
        retry_res = self.engine.cast_vote(session_id="SESS-004", voter_id="V002", candidate_id="C002")
        self.assertTrue(retry_res["success"])
        self.assertEqual(retry_res["block_index"], pre_chain_len)
        self.assertEqual(len(chain.chain), pre_chain_len + 1)
        self.assertTrue(chain.is_chain_valid())
        self.assertTrue(chain.has_voter_voted("V002"))
        self.assertTrue(self.engine.has_voter_voted("SESS-004", "V002"))
        self.assertEqual(self.engine.count_accepted_votes("SESS-004"), 2)
        self.assertEqual(self.engine._tallies["SESS-004"]["C002"], pre_tallies["SESS-004"]["C002"] + 1)
        self.assertEqual(self.engine._total_votes["SESS-004"], pre_total_votes["SESS-004"] + 1)

    def test_two_simultaneous_submissions_same_voter(self):
        """Test 22: Exactly two simultaneous submissions for (session_id, voter_id) yield 1 accepted vote and 1 block."""
        results = []
        errors = []

        def submit():
            try:
                res = self.engine.cast_vote(session_id="SESS-004", voter_id="V002", candidate_id="C003")
                results.append(res)
            except Exception as e:
                errors.append(e)

        with ThreadPoolExecutor(max_workers=2) as executor:
            f1 = executor.submit(submit)
            f2 = executor.submit(submit)
            f1.result()
            f2.result()

        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], DuplicateVoteError)

        chain = self.engine.get_session_chain("SESS-004")
        self.assertEqual(len(chain.chain), 2)  # Genesis + exactly 1 vote block
        self.assertEqual(chain.get_vote_count(), 1)
        self.assertTrue(chain.is_chain_valid())

    def test_receipt_fields_and_voter_linkability_on_ledger(self):
        """Test 23: Receipt contains expected fields; ledger stores voter-linked ciphertext."""
        res = self.engine.cast_vote(session_id="SESS-004", voter_id="V003", candidate_id="C004")

        # Required prototype receipt fields
        required_fields = {
            "success", "vote_id", "session_id", "voter_id",
            "timestamp", "message", "block_index", "block_hash", "num_qubits"
        }
        for field in required_fields:
            self.assertIn(field, res)

        # Plaintext choice is NOT in receipt
        self.assertNotIn("choice_id", res)
        self.assertNotIn("candidate_id", res)
        self.assertNotIn("option_id", res)

        # Ledger linkability: voter_id is stored directly on the Block alongside encrypted_vote
        chain = self.engine.get_session_chain("SESS-004")
        block = chain.chain[res["block_index"]]
        self.assertEqual(block.vote_data["voter_id"], "V003")
        self.assertIn("encrypted_vote", block.vote_data)


class TestM3QuantumEncodingIntegration(unittest.TestCase):
    """
    Dedicated M3 Integration Test Suite:
    Verifies that the integration between M1 session-specific choice pools,
    M2 VotingEngine, and voting.ballot_encoding.encode_choice() is correct, explicit,
    and adheres to quantum encoding and blockchain requirements.
    """

    def setUp(self):
        self.engine = VotingEngine(use_postgres=False)
        # Activate SESS-004 with a valid current voting window so tests
        # never depend on the (possibly stale) CSV timestamps.
        self.engine.set_session_status("SESS-004", "ACTIVE")

    def test_choice_pool_passed_to_encode_choice_comes_from_session_never_global(self):
        """
        M3 Req 1: Confirm that choice pool passed to encode_choice() comes from the
        requested session's assignments, never from the global candidates/options list.
        """
        # 1. candidate_election (SESS-004)
        with patch("voting.voting_engine.encode_choice", wraps=encode_choice) as mock_encode:
            self.engine.cast_vote(session_id="SESS-004", voter_id="V001", candidate_id="C002")
            mock_encode.assert_called_once()
            called_pool = mock_encode.call_args[1]["choice_ids"]
            expected_cands = self.engine.get_session_choices("SESS-004")
            self.assertEqual(called_pool, expected_cands)
            self.assertEqual(len(called_pool), 15)
            self.assertEqual(called_pool, sorted(list(self.engine.session_candidates["SESS-004"])))

        # 2. yes_no with Abstain (SESS-002)
        self.engine.set_session_status("SESS-002", "ACTIVE")
        s2_voter = sorted(list(self.engine.session_voters["SESS-002"]))[0]
        with patch("voting.voting_engine.encode_choice", wraps=encode_choice) as mock_encode:
            self.engine.cast_vote(session_id="SESS-002", voter_id=s2_voter, option_id="O003")
            mock_encode.assert_called_once()
            called_pool = mock_encode.call_args[1]["choice_ids"]
            expected_opts = self.engine.get_session_choices("SESS-002")
            self.assertEqual(called_pool, expected_opts)
            self.assertEqual(called_pool, ["O001", "O002", "O003"])
            self.assertNotIn("O004", called_pool)  # Not global options

        # 3. single_choice (SESS-008)
        self.engine.set_session_status("SESS-008", "ACTIVE")
        s8_voter = sorted(list(self.engine.session_voters["SESS-008"]))[0]
        with patch("voting.voting_engine.encode_choice", wraps=encode_choice) as mock_encode:
            self.engine.cast_vote(session_id="SESS-008", voter_id=s8_voter, option_id="O016")
            mock_encode.assert_called_once()
            called_pool = mock_encode.call_args[1]["choice_ids"]
            expected_opts = self.engine.get_session_choices("SESS-008")
            self.assertEqual(called_pool, expected_opts)
            self.assertEqual(called_pool, ["O016", "O017", "O018"])
            self.assertNotIn("O001", called_pool)  # Not options from other sessions

    def test_unassigned_choice_rejected_before_encoding_without_block_or_tally(self):
        """
        M3 Req 2: A choice that exists globally but is not assigned to the session
        must be rejected without calling encode_choice(), without adding a block,
        and without changing tallies. Tested across all 3 session types.
        """
        # 1. candidate_election: SESS-004 has candidates C001..C015.
        # Temporarily restrict session candidates to C001..C005 in memory for testing
        self.engine.session_candidates["SESS-004"] = {"C001", "C002", "C003", "C004", "C005"}
        chain4 = self.engine.get_session_chain("SESS-004")
        initial_blocks4 = len(chain4.chain)
        tally4_before = self.engine.get_tally("SESS-004")

        with patch("voting.voting_engine.encode_choice") as mock_encode:
            with self.assertRaises(CandidateNotAssignedError):
                # C006 exists globally in candidates.csv, but is not assigned to SESS-004 here
                self.engine.cast_vote(session_id="SESS-004", voter_id="V001", candidate_id="C006")
            mock_encode.assert_not_called()

        self.assertEqual(len(chain4.chain), initial_blocks4)
        self.assertEqual(self.engine.get_tally("SESS-004"), tally4_before)
        self.assertFalse(self.engine.has_voter_voted("SESS-004", "V001"))
        self.assertNotIn(("SESS-004", "V001"), self.engine._reserved_voters)

        # 2. yes_no: SESS-003 has only O001 (Yes) and O002 (No); Abstain (O003) is NOT assigned
        self.engine.set_session_status("SESS-003", "ACTIVE")
        s3_voter = next(iter(self.engine.session_voters["SESS-003"]))
        chain3 = self.engine.get_session_chain("SESS-003")
        initial_blocks3 = len(chain3.chain)
        tally3_before = self.engine.get_tally("SESS-003")

        with patch("voting.voting_engine.encode_choice") as mock_encode:
            with self.assertRaises(OptionNotAssignedError):
                # O003 exists globally in ballot_options.csv, but is not assigned to SESS-003
                self.engine.cast_vote(session_id="SESS-003", voter_id=s3_voter, option_id="O003")
            mock_encode.assert_not_called()

        self.assertEqual(len(chain3.chain), initial_blocks3)
        self.assertEqual(self.engine.get_tally("SESS-003"), tally3_before)
        self.assertFalse(self.engine.has_voter_voted("SESS-003", s3_voter))
        self.assertNotIn(("SESS-003", s3_voter), self.engine._reserved_voters)

        # 3. single_choice: SESS-008 has O016, O017, O018.
        # Option O004 exists globally in ballot_options.csv (assigned to SESS-005), but not SESS-008
        self.engine.set_session_status("SESS-008", "ACTIVE")
        s8_voter = sorted(list(self.engine.session_voters["SESS-008"]))[0]
        chain8 = self.engine.get_session_chain("SESS-008")
        initial_blocks8 = len(chain8.chain)
        tally8_before = self.engine.get_tally("SESS-008")

        with patch("voting.voting_engine.encode_choice") as mock_encode:
            with self.assertRaises(OptionNotAssignedError):
                self.engine.cast_vote(session_id="SESS-008", voter_id=s8_voter, option_id="O004")
            mock_encode.assert_not_called()

        self.assertEqual(len(chain8.chain), initial_blocks8)
        self.assertEqual(self.engine.get_tally("SESS-008"), tally8_before)
        self.assertFalse(self.engine.has_voter_voted("SESS-008", s8_voter))
        self.assertNotIn(("SESS-008", s8_voter), self.engine._reserved_voters)

    def test_encoded_values_are_choice_ids_never_display_labels(self):
        """
        M3 Req 3: Keep choice IDs as the encoded values. Do not encode display
        labels or plaintext voter-choice mappings.
        """
        # In candidate_election, chosen_candidate must be 'C001', not candidate name
        with patch("voting.voting_engine.encode_choice", wraps=encode_choice) as mock_encode:
            res = self.engine.cast_vote(session_id="SESS-004", voter_id="V001", candidate_id="C001")
            call_chosen = mock_encode.call_args[1]["selected_choice_id"]
            self.assertEqual(call_chosen, "C001")
            self.assertNotEqual(call_chosen, self.engine.candidates["C001"]["candidate_name"])

        # In yes_no, chosen_candidate must be 'O001', not 'Yes'
        self.engine.set_session_status("SESS-002", "ACTIVE")
        s2_voter = sorted(list(self.engine.session_voters["SESS-002"]))[0]
        with patch("voting.voting_engine.encode_choice", wraps=encode_choice) as mock_encode:
            res_yn = self.engine.cast_vote(session_id="SESS-002", voter_id=s2_voter, option_id="O001")
            call_chosen_yn = mock_encode.call_args[1]["selected_choice_id"]
            self.assertEqual(call_chosen_yn, "O001")
            self.assertNotEqual(call_chosen_yn, "Yes")

    def test_quantum_encoding_roundtrip_correctness_various_pool_sizes(self):
        """
        M3 Req 4: Tests for encoding round-trip correctness with different numbers
        of assigned choices, including the project's 15-candidate session.
        """
        # Pool size 2 (1 qubit): e.g. Yes/No session (SESS-003)
        pool_2 = ["O001", "O002"]
        for opt in pool_2:
            enc = encode_choice(pool_2, opt)
            self.assertEqual(enc["qubit_count"], 1)
            from voting.ballot_encoding import decode_choice
            self.assertEqual(decode_choice(pool_2, enc["encoded_bits"]), opt)

        # Pool size 3 (2 qubits): e.g. Yes/No/Abstain session (SESS-002)
        pool_3 = ["O001", "O002", "O003"]
        for opt in pool_3:
            enc = encode_choice(pool_3, opt)
            self.assertEqual(enc["qubit_count"], 2)
            self.assertEqual(decode_choice(pool_3, enc["encoded_bits"]), opt)

        # Pool size 5 (3 qubits): e.g. SESS-005 single choice grant funding
        pool_5 = ["O004", "O005", "O006", "O007", "O008"]
        for opt in pool_5:
            enc = encode_choice(pool_5, opt)
            self.assertEqual(enc["qubit_count"], 3)
            self.assertEqual(decode_choice(pool_5, enc["encoded_bits"]), opt)

        # Pool size 15 (4 qubits): full 15 candidates from SESS-004
        pool_15 = [f"C{i:03d}" for i in range(1, 16)]
        self.assertEqual(len(pool_15), 15)
        for cand in pool_15:
            enc = encode_choice(pool_15, cand)
            self.assertEqual(enc["qubit_count"], 4)
            self.assertEqual(decode_choice(pool_15, enc["encoded_bits"]), cand)

    def test_encoding_to_blockchain_association_and_verification(self):
        """
        M3 Req 5: Confirm encoding/decoding returns the same selected ID and that
        the resulting encrypted vote is associated with the correct session's blockchain record.
        """
        # candidate_election
        res = self.engine.cast_vote(session_id="SESS-004", voter_id="V005", candidate_id="C012")
        self.assertEqual(res["num_qubits"], 4)
        ver = self.engine.verify_vote_on_blockchain(
            session_id="SESS-004",
            vote_id=res["vote_id"],
            expected_choice="C012",
        )
        self.assertTrue(ver["verified"])
        self.assertEqual(ver["decrypted_choice"], "C012")
        self.assertEqual(ver["session_id"], "SESS-004")

        # yes_no
        self.engine.set_session_status("SESS-002", "ACTIVE")
        s2_voter = sorted(list(self.engine.session_voters["SESS-002"]))[0]
        res_yn = self.engine.cast_vote(session_id="SESS-002", voter_id=s2_voter, option_id="O003")
        self.assertEqual(res_yn["num_qubits"], 2)
        ver_yn = self.engine.verify_vote_on_blockchain(
            session_id="SESS-002",
            vote_id=res_yn["vote_id"],
            expected_choice="O003",
        )
        self.assertTrue(ver_yn["verified"])
        self.assertEqual(ver_yn["decrypted_choice"], "O003")
        self.assertEqual(ver_yn["session_id"], "SESS-002")

        # single_choice
        self.engine.set_session_status("SESS-008", "ACTIVE")
        s8_voter = sorted(list(self.engine.session_voters["SESS-008"]))[0]
        res_sc = self.engine.cast_vote(session_id="SESS-008", voter_id=s8_voter, option_id="O018")
        self.assertEqual(res_sc["num_qubits"], 2)
        ver_sc = self.engine.verify_vote_on_blockchain(
            session_id="SESS-008",
            vote_id=res_sc["vote_id"],
            expected_choice="O018",
        )
        self.assertTrue(ver_sc["verified"])
        self.assertEqual(ver_sc["decrypted_choice"], "O018")
        self.assertEqual(ver_sc["session_id"], "SESS-008")


def run_all_tests():
    """Console test runner matching repository's prototype test style."""
    print("=" * 64)
    print("M3: INTEGRATED QUANTUM VOTING ENGINE TEST SUITE (M1 DATA MODEL)")
    print("=" * 64)

    engine = VotingEngine(use_postgres=False)
    # Activate SESS-004 with a valid current voting window.
    engine.set_session_status("SESS-004", "ACTIVE")

    # ── Test 1: Successful end-to-end candidate_election vote ──
    print("\n=== Test 1: Successful candidate_election vote (SESS-004) ===")
    res1 = engine.cast_vote("SESS-004", "V001", candidate_id="C002")
    assert res1["success"] is True
    assert res1["block_index"] == 1
    assert "block_hash" in res1 and len(res1["block_hash"]) == 64
    chain4 = engine.get_session_chain("SESS-004")
    assert chain4.get_vote_count() == 1
    assert chain4.is_chain_valid() is True
    print(f"  PASSED: Vote recorded (vote_id={res1['vote_id']}, block={res1['block_index']}, hash={res1['block_hash'][:16]}...)")

    # ── Test 2: Successful yes_no vote with Abstain ──
    print("\n=== Test 2: Successful yes_no votes including Abstain (SESS-002) ===")
    engine.set_session_status("SESS-002", "ACTIVE")
    s2_voters = sorted(list(engine.session_voters["SESS-002"]))
    res_yes = engine.cast_vote("SESS-002", s2_voters[0], option_id="O001")
    res_no = engine.cast_vote("SESS-002", s2_voters[1], option_id="O002")
    res_abs = engine.cast_vote("SESS-002", s2_voters[2], option_id="O003")
    assert res_yes["success"] and res_no["success"] and res_abs["success"]
    tally2 = engine.get_tally("SESS-002")
    assert tally2["O001"] == 1 and tally2["O002"] == 1 and tally2["O003"] == 1
    print(f"  PASSED: Yes, No, and Abstain recorded and tallied: {tally2}")

    # ── Test 3: Successful single_choice policy vote ──
    print("\n=== Test 3: Successful single_choice policy vote (SESS-008) ===")
    engine.set_session_status("SESS-008", "ACTIVE")
    s8_voters = sorted(list(engine.session_voters["SESS-008"]))
    res8 = engine.cast_vote("SESS-008", s8_voters[0], option_id="O016")
    assert res8["success"] is True
    tally8 = engine.get_tally("SESS-008")
    assert tally8["O016"] == 1 and tally8["O017"] == 0
    print(f"  PASSED: Policy vote recorded: {tally8}")

    # ── Test 4: Unknown voter rejected ──
    print("\n=== Test 4: Unknown voter rejected ===")
    try:
        engine.cast_vote("SESS-004", "V999", candidate_id="C002")
        assert False, "Should have raised UnknownVoterError"
    except UnknownVoterError as ex:
        print(f"  PASSED: Correctly rejected ({ex})")

    # ── Test 5: Ineligible voter rejected ──
    print("\n=== Test 5: Ineligible voter rejected on smaller subset session (SESS-003) ===")
    engine.set_session_status("SESS-003", "ACTIVE")
    all_vids = set(engine.voters.keys())
    s3_vids = engine.session_voters["SESS-003"]
    ineligibles = sorted(list(all_vids - s3_vids))
    try:
        engine.cast_vote("SESS-003", ineligibles[0], option_id="O001")
        assert False, "Should have raised IneligibleVoterError"
    except IneligibleVoterError as ex:
        print(f"  PASSED: Correctly rejected unenrolled voter {ineligibles[0]} ({ex})")

    # ── Test 6: Inactive and out-of-window session rejected ──
    print("\n=== Test 6: Inactive session rejected ===")
    try:
        engine.cast_vote("SESS-001", "V001", candidate_id="C001")
        assert False, "Should have raised InactiveSessionError for COMPLETED"
    except InactiveSessionError as ex:
        print(f"  PASSED: COMPLETED session rejected ({ex})")

    try:
        engine.cast_vote("SESS-005", "V001", option_id="O004")
        assert False, "Should have raised InactiveSessionError for UPCOMING"
    except InactiveSessionError as ex:
        print(f"  PASSED: UPCOMING session rejected ({ex})")

    # ── Test 7: Wrong-type choice rejected ──
    print("\n=== Test 7: Wrong-type choice rejected ===")
    try:
        engine.cast_vote("SESS-004", "V010", choice_id="O001")
        assert False, "Should have raised InvalidChoiceError"
    except InvalidChoiceError as ex:
        print(f"  PASSED: Option rejected in candidate_election ({ex})")

    try:
        engine.cast_vote("SESS-002", s2_voters[3], choice_id="C001")
        assert False, "Should have raised InvalidChoiceError"
    except InvalidChoiceError as ex:
        print(f"  PASSED: Candidate rejected in yes_no ({ex})")

    # ── Test 8: Duplicate vote rejected ──
    print("\n=== Test 8: Duplicate vote in same session rejected ===")
    try:
        engine.cast_vote("SESS-004", "V001", candidate_id="C007")
        assert False, "Should have raised DuplicateVoteError"
    except DuplicateVoteError as ex:
        print(f"  PASSED: Duplicate vote rejected ({ex})")

    # ── Test 9: Concurrency safety ──
    print("\n=== Test 9: Concurrent submissions for same voter produce exactly one vote ===")
    results = []
    errors = []

    def submit_concurrent(idx):
        try:
            res = engine.cast_vote("SESS-004", "V020", candidate_id="C005")
            results.append((idx, res))
        except Exception as e:
            errors.append((idx, type(e)))

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(submit_concurrent, i) for i in range(5)]
        for f in futures:
            f.result()

    assert len(results) == 1, f"Expected 1 winner, got {len(results)}"
    assert len(errors) == 4, f"Expected 4 errors, got {len(errors)}"
    for _, err_t in errors:
        assert err_t == DuplicateVoteError
    print(f"  PASSED: 5 concurrent threads -> exactly 1 vote accepted, 4 rejected with DuplicateVoteError")

    # ── Test 10: Blockchain decryption & verification ──
    print("\n=== Test 10: Blockchain decryption & verification ===")
    ver = engine.verify_vote_on_blockchain("SESS-004", res1["vote_id"], expected_choice="C002")
    assert ver["verified"] is True
    assert ver["decrypted_choice"] == "C002"
    print(f"  PASSED: Block #{ver['block_index']} decrypted to: {ver['decrypted_choice']}")

    print("\n" + "=" * 64)
    print("  ALL M3 QUANTUM VOTING ENGINE TESTS PASSED")
    print("=" * 64)


if __name__ == "__main__":
    run_all_tests()
