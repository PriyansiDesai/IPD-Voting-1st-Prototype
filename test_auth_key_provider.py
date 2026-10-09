import unittest
import os
from unittest.mock import patch, MagicMock
from voting.voting_engine import VotingEngine, BB84SecurityError, VotingError
from voting.classical_channel import MAX_FRAMES

REQUIRED_LEN = 16 + MAX_FRAMES * 16

class TestAuthKeyProvider(unittest.TestCase):

    def setUp(self):
        self.keys_generated = []
        def auth_provider(req_len):
            key = os.urandom(req_len)
            self.keys_generated.append(key)
            return key
        self.auth_provider = auth_provider

    @patch('voting.voting_engine.run_secure_bb84')
    def test_in_memory_auth_key_provider(self, mock_run_secure):
        mock_run_secure.return_value = {"secure": True, "aborted": False, "final_key": [0]*256}
        engine = VotingEngine(use_postgres=False, auth_key_provider=self.auth_provider)
        engine.set_session_status("SESS-004", "ACTIVE")
        
        # Run 1
        res1 = engine.cast_vote("SESS-004", "V001", candidate_id="C002")
        self.assertTrue(res1["success"])
        
        # Run 2
        res2 = engine.cast_vote("SESS-004", "V002", candidate_id="C003")
        self.assertTrue(res2["success"])
        
        self.assertEqual(len(self.keys_generated), 2)
        self.assertNotEqual(self.keys_generated[0], self.keys_generated[1])
        
        self.assertEqual(mock_run_secure.call_count, 2)
        call1_kwargs = mock_run_secure.call_args_list[0][1]
        call2_kwargs = mock_run_secure.call_args_list[1][1]
        self.assertEqual(call1_kwargs.get("auth_key"), self.keys_generated[0])
        self.assertEqual(call2_kwargs.get("auth_key"), self.keys_generated[1])

    @patch('voting.voting_engine.encrypt_vote')
    @patch('voting.voting_engine.run_secure_bb84')
    def test_in_memory_auth_key_provider_invalid_length(self, mock_run, mock_encrypt):
        engine = VotingEngine(use_postgres=False, auth_key_provider=lambda r: b"too_short")
        engine.set_session_status("SESS-004", "ACTIVE")
        
        with self.assertRaises(VotingError) as ctx:
            engine.cast_vote("SESS-004", "V001", candidate_id="C002")
        self.assertIn("Simulation-only provider returned invalid auth_key", str(ctx.exception))
        
        mock_run.assert_not_called()
        mock_encrypt.assert_not_called()
        self.assertNotIn(("SESS-004", "V001"), engine._reserved_voters)

    @patch('voting.voting_engine.encrypt_vote')
    @patch('voting.voting_engine.run_secure_bb84')
    def test_in_memory_auth_key_provider_invalid_type(self, mock_run, mock_encrypt):
        engine = VotingEngine(use_postgres=False, auth_key_provider=lambda r: 12345)
        engine.set_session_status("SESS-004", "ACTIVE")
        
        with self.assertRaises(VotingError) as ctx:
            engine.cast_vote("SESS-004", "V001", candidate_id="C002")
        self.assertIn("Simulation-only provider returned invalid auth_key", str(ctx.exception))
        
        mock_run.assert_not_called()
        mock_encrypt.assert_not_called()
        self.assertNotIn(("SESS-004", "V001"), engine._reserved_voters)

    @patch('voting.voting_engine.encrypt_vote')
    @patch('voting.voting_engine.run_secure_bb84')
    def test_in_memory_auth_key_provider_error(self, mock_run, mock_encrypt):
        def bad_provider(req_len):
            raise RuntimeError("Provider failed")
            
        engine = VotingEngine(use_postgres=False, auth_key_provider=bad_provider)
        engine.set_session_status("SESS-004", "ACTIVE")
        
        with self.assertRaises(RuntimeError) as ctx:
            engine.cast_vote("SESS-004", "V001", candidate_id="C002")
        self.assertEqual(str(ctx.exception), "Provider failed")
        
        mock_run.assert_not_called()
        mock_encrypt.assert_not_called()
        self.assertNotIn(("SESS-004", "V001"), engine._reserved_voters)


    @patch('voting.voting_engine.run_secure_bb84')
    @patch.dict('os.environ', {"DATABASE_URL": "postgres://fake"})
    def test_postgres_auth_key_provider(self, mock_run_secure):
        mock_run_secure.return_value = {"secure": True, "aborted": False, "final_key": [0]*256}
        
        with patch('voting.postgres_db.PostgresVotingRepository') as MockRepo:
            engine = VotingEngine(use_postgres=True, auth_key_provider=self.auth_provider)
            repo_instance = MockRepo.return_value
            repo_instance.validate_choice.return_value = True
            repo_instance.reserve_vote.return_value = (True, "token", None)
            repo_instance.get_session_choices.return_value = ["C002", "C003"]
            repo_instance.finalize_vote.return_value = "receipt_123"
            
            engine.repo = repo_instance
            
            res1 = engine.cast_vote("SESS-004", "V001", candidate_id="C002")
            self.assertEqual(res1["status"], "success")
            
            self.assertEqual(len(self.keys_generated), 1)
            self.assertEqual(mock_run_secure.call_count, 1)
            self.assertEqual(mock_run_secure.call_args[1].get("auth_key"), self.keys_generated[0])


    @patch('voting.voting_engine.encrypt_vote')
    @patch('voting.voting_engine.run_secure_bb84')
    @patch.dict('os.environ', {"DATABASE_URL": "postgres://fake"})
    def test_postgres_auth_key_provider_invalid_length(self, mock_run, mock_encrypt):
        with patch('voting.postgres_db.PostgresVotingRepository') as MockRepo:
            engine = VotingEngine(use_postgres=True, auth_key_provider=lambda r: b"too_short")
            repo_instance = MockRepo.return_value
            repo_instance.validate_choice.return_value = True
            repo_instance.reserve_vote.return_value = (True, "token", None)
            repo_instance.get_session_choices.return_value = ["C002", "C003"]
            engine.repo = repo_instance
            
            with self.assertRaises(VotingError) as ctx:
                engine.cast_vote("SESS-004", "V001", candidate_id="C002")
            self.assertIn("Cryptographic pipeline failed:", str(ctx.exception))
            self.assertIn("Simulation-only provider returned invalid auth_key", str(ctx.exception))
            
            mock_run.assert_not_called()
            mock_encrypt.assert_not_called()
            repo_instance.finalize_vote.assert_not_called()

    @patch('voting.voting_engine.encrypt_vote')
    @patch('voting.voting_engine.run_secure_bb84')
    @patch.dict('os.environ', {"DATABASE_URL": "postgres://fake"})
    def test_postgres_auth_key_provider_invalid_type(self, mock_run, mock_encrypt):
        with patch('voting.postgres_db.PostgresVotingRepository') as MockRepo:
            engine = VotingEngine(use_postgres=True, auth_key_provider=lambda r: 12345)
            repo_instance = MockRepo.return_value
            repo_instance.validate_choice.return_value = True
            repo_instance.reserve_vote.return_value = (True, "token", None)
            repo_instance.get_session_choices.return_value = ["C002", "C003"]
            engine.repo = repo_instance
            
            with self.assertRaises(VotingError) as ctx:
                engine.cast_vote("SESS-004", "V001", candidate_id="C002")
            self.assertIn("Cryptographic pipeline failed:", str(ctx.exception))
            self.assertIn("Simulation-only provider returned invalid auth_key", str(ctx.exception))
            
            mock_run.assert_not_called()
            mock_encrypt.assert_not_called()
            repo_instance.finalize_vote.assert_not_called()

    @patch('voting.voting_engine.encrypt_vote')
    @patch('voting.voting_engine.run_secure_bb84')
    @patch.dict('os.environ', {"DATABASE_URL": "postgres://fake"})
    def test_postgres_auth_key_provider_error(self, mock_run, mock_encrypt):
        def bad_provider(req_len):
            raise RuntimeError("Provider failed")
            
        with patch('voting.postgres_db.PostgresVotingRepository') as MockRepo:
            engine = VotingEngine(use_postgres=True, auth_key_provider=bad_provider)
            repo_instance = MockRepo.return_value
            repo_instance.validate_choice.return_value = True
            repo_instance.reserve_vote.return_value = (True, "token", None)
            repo_instance.get_session_choices.return_value = ["C002", "C003"]
            engine.repo = repo_instance
            
            with self.assertRaises(VotingError) as ctx:
                engine.cast_vote("SESS-004", "V001", candidate_id="C002")
            self.assertIn("Cryptographic pipeline failed:", str(ctx.exception))
            self.assertIn("Provider failed", str(ctx.exception))
            
            mock_run.assert_not_called()
            mock_encrypt.assert_not_called()
            repo_instance.finalize_vote.assert_not_called()


if __name__ == '__main__':
    unittest.main()
