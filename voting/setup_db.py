import os
import sys
import csv
from pathlib import Path
from voting.postgres_db import PostgresVotingRepository

def get_csv_count(path: Path) -> int:
    if not path.exists():
        return 0
    with open(path, 'r', encoding='utf-8') as f:
        return sum(1 for _ in csv.DictReader(f))

def setup():
    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        print('DATABASE_URL is required.')
        sys.exit(1)
        
    try:
        repo = PostgresVotingRepository(db_url)
        repo.run_migrations()
        
        data_dir = Path(__file__).parent.parent / 'data'
        
        expected_counts = {
            'voters': get_csv_count(data_dir / 'voters.csv'),
            'candidates': get_csv_count(data_dir / 'candidates.csv'),
            'ballot_options': get_csv_count(data_dir / 'ballot_options.csv'),
            'voting_sessions': get_csv_count(data_dir / 'voting_sessions.csv'),
            'session_voters': get_csv_count(data_dir / 'session_voters.csv'),
            'session_choices': get_csv_count(data_dir / 'session_candidates.csv') + get_csv_count(data_dir / 'session_options.csv')
        }
        
        conn = repo.get_connection()
        try:
            with conn.cursor() as cur:
                db_counts = {}
                for t in expected_counts:
                    cur.execute(f"SELECT COUNT(*) FROM {t}")
                    db_counts[t] = cur.fetchone()[0]
                
                total_db = sum(db_counts.values())
                if total_db == 0:
                    pass # completely empty, allow import
                elif db_counts == expected_counts:
                    print("Fixtures already imported. Skipping.")
                    sys.exit(0)
                else:
                    print("Error: Partial import detected. Please reset only the disposable test database before rerunning setup.")
                    sys.exit(1)
        finally:
            repo.pool.putconn(conn)
            
        repo.import_csv_fixtures(data_dir)
        print('Database setup complete.')
    except Exception as e:
        print(f"Setup failed: {e}")
        sys.exit(1)

if __name__ == '__main__':
    setup()
