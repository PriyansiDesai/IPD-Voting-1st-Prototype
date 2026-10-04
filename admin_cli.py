import argparse
import sys
import os
from voting.admin_provisioning_repo import AdminProvisioningRepository

def main():
    parser = argparse.ArgumentParser(description="Issue a one-time provisioning token for a voter.")
    parser.add_argument("voter_id", help="The internal voter ID")
    parser.add_argument("--expires-in", type=int, default=60, help="Expiration in minutes (default 60)")
    
    args = parser.parse_args()
    
    dsn = os.environ.get("ADMIN_DATABASE_URL")
    if not dsn:
        print("Error: ADMIN_DATABASE_URL environment variable is not set.", file=sys.stderr)
        sys.exit(1)
        
    repo = AdminProvisioningRepository(dsn)
    try:
        token = repo.generate_token(args.voter_id, args.expires_in)
        print("Provisioning Token Generated Successfully.")
        print("Securely deliver the following token to the voter:")
        print(f"\n{token}\n")
    except Exception as e:
        print(f"Error generating token: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
