import unittest
from unittest.mock import patch
from pathlib import Path
import sys

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from voting.voting_engine import VotingEngine, VotingError
from voting.ballot_encoding import encode_choice

class TestM3Integration(unittest.TestCase):
    def setUp(self):
        self.engine = VotingEngine(use_postgres=False)
        self.engine.set_session_status("SESS-004", "ACTIVE")

    @patch('voting.voting_engine.encode_choice')
    def test_m3_encoding_failure_stops_pipeline(self, mock_encode_choice):
        """M3 Integration: If encoding fails, ensure pipeline stops and reservation is not finalized."""
        mock_encode_choice.side_effect = ValueError("M3 Encoding Failed")

        participation_key = ("SESS-004", "V001")

        with self.assertRaises(ValueError) as context:
            self.engine.cast_vote(
                session_id="SESS-004",
                voter_id="V001",
                candidate_id="C002",
            )

        self.assertIn("M3 Encoding Failed", str(context.exception))

        # Ensure not in voted voters
        self.assertNotIn(participation_key, self.engine._voted_voters)
        # Ensure no block was added
        chain = self.engine.get_session_chain("SESS-004")
        self.assertEqual(len(chain.chain), 1) # only genesis
        # Ensure reservation was released in the in-memory path
        self.assertNotIn(participation_key, self.engine._reserved_voters)

    @patch('voting.voting_engine.encode_choice', wraps=encode_choice)
    def test_m3_valid_vote_integration(self, spy_encode):
        """M3 Integration: Prove a valid vote reaches M3 and successfully commits."""
        result = self.engine.cast_vote(
            session_id="SESS-004",
            voter_id="V002",
            candidate_id="C001",
        )
        self.assertTrue(result["success"])
        spy_encode.assert_called_once()
        args, kwargs = spy_encode.call_args
        self.assertEqual(kwargs["selected_choice_id"], "C001")
        # Ensure the vote committed successfully
        participation_key = ("SESS-004", "V002")
        self.assertIn(participation_key, self.engine._voted_voters)

    @patch('voting.voting_engine.encrypt_vote')
    @patch('voting.voting_engine.run_secure_bb84')
    @patch('voting.voting_engine.encode_choice')
    def test_m3_encoding_failure_stops_pipeline_postgres(self, mock_encode, mock_bb84, mock_encrypt):
        """M3 Integration (PostgreSQL): If encoding fails, ensure pipeline stops and reservation is not finalized."""
        from unittest.mock import patch
        import os

        class FakePostgresRepo:
            def __init__(self):
                self.participation_state = None
                self.finalize_called = False

            def validate_choice(self, session_id, choice):
                return True

            def reserve_vote(self, session_id, voter_id, idempotency_key):
                self.participation_state = "PENDING"
                return (True, "fake-token", None)

            def get_session_choices(self, session_id):
                return ["C001", "C002"]

            def finalize_vote(self, session_id, voter_id, reservation_token, encrypted_payload):
                self.participation_state = "COMMITTED"
                self.finalize_called = True
                return "fake-receipt"

        # Set up engine in postgres mode, patching PostgresVotingRepository to avoid connection
        with patch.dict(os.environ, {'DATABASE_URL': 'postgresql://mock:mock@localhost/mock'}):
            with patch('voting.postgres_db.PostgresVotingRepository'):
                engine = VotingEngine(use_postgres=True)

        fake_repo = FakePostgresRepo()
        engine.repo = fake_repo

        # Encoding raises an error
        mock_encode.side_effect = ValueError("M3 Encoding Failed")

        with self.assertRaises(VotingError) as context:
            engine.cast_vote(
                session_id="SESS-004",
                voter_id="V001",
                candidate_id="C002",
            )

        self.assertIn("M3 Encoding Failed", str(context.exception))

        # Assert finalize_vote is never called and state is PENDING
        self.assertFalse(fake_repo.finalize_called)
        self.assertEqual(fake_repo.participation_state, "PENDING")

        # Assert BB84 and PQC are not called after encoding fails
        mock_bb84.assert_not_called()
        mock_encrypt.assert_not_called()

    @patch('voting.voting_engine.encrypt_vote')
    @patch('voting.voting_engine.run_secure_bb84')
    @patch('voting.voting_engine.prepare_circuit')
    def test_m3_circuit_preparation_failure_stops_pipeline(self, mock_prepare, mock_bb84, mock_encrypt):
        """M3 Integration: If circuit preparation fails, ensure pipeline stops and reservation is not finalized."""
        from unittest.mock import patch
        import os

        class FakePostgresRepo:
            def __init__(self):
                self.participation_state = None
                self.finalize_called = False
            def validate_choice(self, session_id, choice):
                return True
            def reserve_vote(self, session_id, voter_id, idempotency_key):
                self.participation_state = "PENDING"
                return (True, "fake-token", None)
            def get_session_choices(self, session_id):
                return ["C001", "C002"]
            def finalize_vote(self, session_id, voter_id, reservation_token, encrypted_payload):
                self.participation_state = "COMMITTED"
                self.finalize_called = True
                return "fake-receipt"

        with patch.dict(os.environ, {'DATABASE_URL': 'postgresql://mock:mock@localhost/mock'}):
            with patch('voting.postgres_db.PostgresVotingRepository'):
                engine = VotingEngine(use_postgres=True)

        fake_repo = FakePostgresRepo()
        engine.repo = fake_repo

        # prepare_circuit raises an error
        mock_prepare.side_effect = ValueError("M3 Circuit Preparation Failed")

        with self.assertRaises(VotingError) as context:
            engine.cast_vote(
                session_id="SESS-004",
                voter_id="V001",
                candidate_id="C002",
            )

        self.assertIn("M3 Circuit Preparation Failed", str(context.exception))

        # Assert finalize_vote is never called and state is PENDING
        self.assertFalse(fake_repo.finalize_called)
        self.assertEqual(fake_repo.participation_state, "PENDING")

        # Assert BB84 and PQC are not called
        mock_bb84.assert_not_called()
        mock_encrypt.assert_not_called()

    @patch('voting.voting_engine.encrypt_vote')
    @patch('voting.voting_engine.M3State')
    @patch('voting.voting_engine.prepare_circuit')
    def test_m3_valid_vote_circuit_preparation(self, spy_prepare, spy_m3state, mock_encrypt):
        """M3 Integration: Prove a valid vote prepares the circuit and uses the internal representation, with no measurements."""
        from voting.ballot_encoding import prepare_circuit, M3State
        spy_prepare.side_effect = prepare_circuit
        spy_m3state.side_effect = M3State
        mock_encrypt.return_value = b'mock_ciphertext'

        result = self.engine.cast_vote(
            session_id="SESS-004",
            voter_id="V002",
            candidate_id="C002",
        )
        self.assertTrue(result["success"])
        spy_prepare.assert_called_once()
        spy_m3state.assert_called_once()
        mock_encrypt.assert_called_once()

        # Verify M3State was created with the correct parameters
        m3state_args, m3state_kwargs = spy_m3state.call_args
        self.assertEqual(m3state_kwargs.get("encoded_bits"), "0001")

        # Extract the production-path circuit and verify it has the expected qubit count and zero measurements
        circuit = m3state_kwargs.get("circuit")
        self.assertIsNotNone(circuit)
        self.assertEqual(circuit.num_qubits, 4)
        self.assertEqual(circuit.count_ops().get("measure", 0), 0)

        # Ensure the versioned result, encoded_bits, and internal representation are NOT exposed in the public vote response
        self.assertNotIn("m3_result", result)
        self.assertNotIn("encoded_bits", result)
        self.assertNotIn("m3_state", result)
        self.assertNotIn("circuit", result)

        # Verify encrypt_vote received the encoded_bits
        encrypt_args, encrypt_kwargs = mock_encrypt.call_args
        actual_encrypt_input = encrypt_args[0] if len(encrypt_args) > 0 else encrypt_kwargs.get("vote_data")
        self.assertEqual(actual_encrypt_input, "0001")

if __name__ == '__main__':
    unittest.main()
