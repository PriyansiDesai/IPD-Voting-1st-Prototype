CREATE TABLE IF NOT EXISTS voter_identities (
    issuer TEXT NOT NULL,
    subject TEXT NOT NULL,
    voter_id TEXT NOT NULL REFERENCES voters(voter_id),
    PRIMARY KEY (issuer, subject)
);

-- Note: The role voting_app might not exist in the test DB, 
-- but in production we grant privileges.
DO
$do$
BEGIN
   IF EXISTS (
      SELECT FROM pg_catalog.pg_roles
      WHERE  rolname = 'voting_app') THEN
      GRANT SELECT ON voter_identities TO voting_app;
      REVOKE INSERT, UPDATE, DELETE ON voter_identities FROM voting_app;
   END IF;
END
$do$;
