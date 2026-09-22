"""
test_voting_engine.py
M2: Test suite for the Multi-Voter Voting Engine.

Covers:
- valid vote accepted
- unknown voter rejected
- ineligible voter rejected
- inactive session rejected
- invalid candidate rejected
- candidate not assigned to session rejected
- duplicate vote in same session rejected
- same voter voting in another session accepted
- accepted vote counting and tallying
"""

import sys
from pathlib import Path
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


class TestVotingEngine(unittest.TestCase):
    """Unit test cases for VotingEngine."""

    def setUp(self):
        # Create fresh engine instance before each test
        self.engine = VotingEngine()

    def test_valid_vote_accepted(self):
        """Test A: Valid eligible voter casting vote for assigned candidate in ACTIVE session."""
        result = self.engine.cast_vote(
            session_id="SESS-003",
            voter_id="V001",
            candidate_id="C002",
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["session_id"], "SESS-003")
        self.assertEqual(result["voter_id"], "V001")
        self.assertEqual(result["candidate_id"], "C002")
        self.assertTrue(self.engine.has_voter_voted("SESS-003", "V001"))
        self.assertEqual(self.engine.count_accepted_votes("SESS-003"), 1)

    def test_unknown_voter_rejected(self):
        """Test B: Unknown voter ID is rejected."""
        with self.assertRaises(UnknownVoterError):
            self.engine.cast_vote(
                session_id="SESS-003",
                voter_id="V999",
                candidate_id="C002",
            )

    def test_ineligible_voter_rejected(self):
        """Test C: Known voter who is not enrolled in the session is rejected."""
        # V002 exists in voters.csv but is not in session_voters for SESS-003
        self.assertIn("V002", self.engine.voters)
        self.assertNotIn("V002", self.engine.session_voters["SESS-003"])
        with self.assertRaises(IneligibleVoterError):
            self.engine.cast_vote(
                session_id="SESS-003",
                voter_id="V002",
                candidate_id="C002",
            )

    def test_inactive_session_rejected(self):
        """Test D: Sessions not in ACTIVE status (COMPLETED, UPCOMING) are rejected."""
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
        """Test E: Unknown candidate ID is rejected."""
        with self.assertRaises(UnknownCandidateError):
            self.engine.cast_vote(
                session_id="SESS-003",
                voter_id="V001",
                candidate_id="C999",
            )

    def test_candidate_not_assigned_to_session_rejected(self):
        """Test F: Known candidate not assigned to the selected session is rejected."""
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
        """Test G: Second vote by the same voter within the same session is rejected."""
        self.engine.cast_vote("SESS-003", "V001", "C002")
        self.assertEqual(self.engine.count_accepted_votes("SESS-003"), 1)

        # Attempting second vote in SESS-003
        with self.assertRaises(DuplicateVoteError):
            self.engine.cast_vote("SESS-003", "V001", "C011")

        # Vote count should remain 1
        self.assertEqual(self.engine.count_accepted_votes("SESS-003"), 1)

    def test_same_voter_in_another_session_accepted(self):
        """Test H: The same voter is permitted to vote in multiple different sessions."""
        # 1. Vote in SESS-003 (ACTIVE)
        res1 = self.engine.cast_vote("SESS-003", "V001", "C002")
        self.assertTrue(res1["success"])

        # 2. Activate SESS-004 in memory on this engine instance without touching CSV
        self.engine.set_session_status("SESS-004", "ACTIVE")

        # V001 is also eligible for SESS-004, and C001 is assigned to SESS-004
        self.assertIn("V001", self.engine.session_voters["SESS-004"])
        self.assertIn("C001", self.engine.session_candidates["SESS-004"])

        res2 = self.engine.cast_vote("SESS-004", "V001", "C001")
        self.assertTrue(res2["success"])

        # Verification: voter has voted in both sessions
        self.assertTrue(self.engine.has_voter_voted("SESS-003", "V001"))
        self.assertTrue(self.engine.has_voter_voted("SESS-004", "V001"))
        self.assertEqual(self.engine.count_accepted_votes("SESS-003"), 1)
        self.assertEqual(self.engine.count_accepted_votes("SESS-004"), 1)
        self.assertEqual(self.engine.count_accepted_votes(), 2)

    def test_count_accepted_votes_and_tally(self):
        """Test I: Vote count filtering and session tally calculation."""
        self.assertEqual(self.engine.count_accepted_votes(), 0)

        # Cast two votes in SESS-003: V001 votes C002, V003 votes C002, V004 votes C007
        self.engine.cast_vote("SESS-003", "V001", "C002")
        self.engine.cast_vote("SESS-003", "V003", "C002")
        self.engine.cast_vote("SESS-003", "V004", "C007")

        self.assertEqual(self.engine.count_accepted_votes("SESS-003"), 3)
        self.assertEqual(self.engine.count_accepted_votes("SESS-003", "C002"), 2)
        self.assertEqual(self.engine.count_accepted_votes("SESS-003", "C007"), 1)
        self.assertEqual(self.engine.count_accepted_votes("SESS-003", "C011"), 0)

        tally = self.engine.get_tally("SESS-003")
        self.assertEqual(tally["C002"], 2)
        self.assertEqual(tally["C007"], 1)
        self.assertEqual(tally["C011"], 0)


def run_all_tests():
    """Console test runner matching repository's prototype test style."""
    print("=" * 60)
    print("M2: VOTING ENGINE TEST SUITE")
    print("=" * 60)

    engine = VotingEngine()

    # ── Test 1: Valid vote accepted ──
    print("\n=== Test 1: Valid vote accepted ===")
    res = engine.cast_vote("SESS-003", "V001", "C002")
    assert res["success"] is True
    assert engine.has_voter_voted("SESS-003", "V001") is True
    assert engine.count_accepted_votes("SESS-003") == 1
    print(f"  PASSED: Vote recorded (vote_id={res['vote_id']})")

    # ── Test 2: Unknown voter rejected ──
    print("\n=== Test 2: Unknown voter rejected ===")
    try:
        engine.cast_vote("SESS-003", "V999", "C002")
        print("  FAILED: Should have raised UnknownVoterError")
        assert False
    except UnknownVoterError as ex:
        print(f"  PASSED: Correctly rejected ({ex})")

    # ── Test 3: Ineligible voter rejected ──
    print("\n=== Test 3: Ineligible voter rejected ===")
    try:
        engine.cast_vote("SESS-003", "V002", "C002")
        print("  FAILED: Should have raised IneligibleVoterError")
        assert False
    except IneligibleVoterError as ex:
        print(f"  PASSED: Correctly rejected ({ex})")

    # ── Test 4: Inactive session rejected ──
    print("\n=== Test 4: Inactive session rejected ===")
    try:
        engine.cast_vote("SESS-001", "V001", "C008")
        print("  FAILED: Should have raised InactiveSessionError for COMPLETED session")
        assert False
    except InactiveSessionError as ex:
        print(f"  PASSED: COMPLETED session rejected ({ex})")

    try:
        engine.cast_vote("SESS-004", "V001", "C001")
        print("  FAILED: Should have raised InactiveSessionError for UPCOMING session")
        assert False
    except InactiveSessionError as ex:
        print(f"  PASSED: UPCOMING session rejected ({ex})")

    # ── Test 5: Invalid candidate rejected ──
    print("\n=== Test 5: Invalid candidate rejected ===")
    try:
        engine.cast_vote("SESS-003", "V003", "C999")
        print("  FAILED: Should have raised UnknownCandidateError")
        assert False
    except UnknownCandidateError as ex:
        print(f"  PASSED: Correctly rejected ({ex})")

    # ── Test 6: Candidate not assigned to session rejected ──
    print("\n=== Test 6: Candidate not assigned to session rejected ===")
    try:
        engine.cast_vote("SESS-003", "V003", "C001")
        print("  FAILED: Should have raised CandidateNotAssignedError")
        assert False
    except CandidateNotAssignedError as ex:
        print(f"  PASSED: Correctly rejected ({ex})")

    # ── Test 7: Duplicate vote in same session rejected ──
    print("\n=== Test 7: Duplicate vote in same session rejected ===")
    try:
        engine.cast_vote("SESS-003", "V001", "C011")
        print("  FAILED: Should have raised DuplicateVoteError")
        assert False
    except DuplicateVoteError as ex:
        print(f"  PASSED: Correctly rejected duplicate vote ({ex})")

    # ── Test 8: Same voter voting in another session accepted ──
    print("\n=== Test 8: Same voter voting in another session accepted ===")
    engine.set_session_status("SESS-004", "ACTIVE")
    res_sess4 = engine.cast_vote("SESS-004", "V001", "C001")
    assert res_sess4["success"] is True
    assert engine.has_voter_voted("SESS-003", "V001") is True
    assert engine.has_voter_voted("SESS-004", "V001") is True
    print(f"  PASSED: V001 successfully voted in SESS-003 and SESS-004")

    # ── Test 9: Vote count and tally verification ──
    print("\n=== Test 9: Vote count and tally verification ===")
    # Add a second vote to SESS-003
    engine.cast_vote("SESS-003", "V003", "C002")
    sess3_count = engine.count_accepted_votes("SESS-003")
    sess4_count = engine.count_accepted_votes("SESS-004")
    total_count = engine.count_accepted_votes()
    assert sess3_count == 2
    assert sess4_count == 1
    assert total_count == 3
    tally = engine.get_tally("SESS-003")
    assert tally["C002"] == 2
    assert tally["C007"] == 0
    assert tally["C011"] == 0
    print(f"  PASSED: Total votes={total_count}, SESS-003={sess3_count}, SESS-004={sess4_count}")
    print(f"  SESS-003 Candidate Tally: {tally}")

    print("\n" + "=" * 60)
    print("  ALL M2 VOTING ENGINE TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    run_all_tests()
