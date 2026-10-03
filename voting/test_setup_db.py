import unittest
from unittest.mock import patch, MagicMock
from voting.setup_db import setup

class TestSetupDB(unittest.TestCase):
    @patch('voting.setup_db.os.environ.get')
    @patch('voting.setup_db.PostgresVotingRepository')
    @patch('voting.setup_db.get_csv_count')
    @patch('voting.setup_db.sys.exit', side_effect=SystemExit)
    def test_setup_empty(self, mock_exit, mock_get_csv, mock_repo_cls, mock_env):
        mock_env.return_value = 'dummy_url'
        mock_repo = MagicMock()
        mock_repo_cls.return_value = mock_repo
        
        mock_get_csv.side_effect = lambda path: 5 # 5 expected for each
        
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_repo.get_connection.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        
        # db counts return 0 (empty)
        mock_cur.fetchone.return_value = [0]
        
        try:
            setup()
        except SystemExit:
            pass
        
        mock_repo.import_csv_fixtures.assert_called_once()
        mock_exit.assert_not_called()

    @patch('voting.setup_db.os.environ.get')
    @patch('voting.setup_db.PostgresVotingRepository')
    @patch('voting.setup_db.get_csv_count')
    @patch('voting.setup_db.sys.exit', side_effect=SystemExit)
    def test_setup_complete(self, mock_exit, mock_get_csv, mock_repo_cls, mock_env):
        mock_env.return_value = 'dummy_url'
        mock_repo = MagicMock()
        mock_repo_cls.return_value = mock_repo
        
        def mock_csv_side_effect(path):
            if 'session_candidates' in str(path) or 'session_options' in str(path):
                return 5
            return 10
        mock_get_csv.side_effect = mock_csv_side_effect
        
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_repo.get_connection.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        
        # db counts match exactly
        mock_cur.fetchone.side_effect = [[10], [10], [10], [10], [10], [10]]
        
        try:
            setup()
        except SystemExit:
            pass
        
        mock_repo.import_csv_fixtures.assert_not_called()
        mock_exit.assert_called_once_with(0)

    @patch('voting.setup_db.os.environ.get')
    @patch('voting.setup_db.PostgresVotingRepository')
    @patch('voting.setup_db.get_csv_count')
    @patch('voting.setup_db.sys.exit', side_effect=SystemExit)
    def test_setup_partial(self, mock_exit, mock_get_csv, mock_repo_cls, mock_env):
        mock_env.return_value = 'dummy_url'
        mock_repo = MagicMock()
        mock_repo_cls.return_value = mock_repo
        
        mock_get_csv.return_value = 10
        
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_repo.get_connection.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        
        # db counts don't match (e.g. 5 instead of 10)
        mock_cur.fetchone.side_effect = [[10], [5], [10], [10], [10], [20]]
        
        try:
            setup()
        except SystemExit:
            pass
        
        mock_repo.import_csv_fixtures.assert_not_called()
        mock_exit.assert_called_once_with(1)

if __name__ == '__main__':
    unittest.main()
