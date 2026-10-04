CREATE TABLE IF NOT EXISTS provisioning_tokens (
    token_hash TEXT PRIMARY KEY,
    voter_id TEXT NOT NULL REFERENCES voters(voter_id),
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    consumed_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Note: The following grants are required for the admin provisioning service to operate securely.
-- DO NOT include database passwords in migrations.
-- A DBA must execute these in production to set up the 'voting_admin' role:
--
-- CREATE ROLE voting_admin WITH LOGIN PASSWORD '<SECURE_ADMIN_PASSWORD>';
-- GRANT CONNECT ON DATABASE ipd_voting_prod TO voting_admin;
-- GRANT USAGE ON SCHEMA public TO voting_admin;
-- GRANT SELECT ON voters TO voting_admin;
-- GRANT SELECT, INSERT, UPDATE ON provisioning_tokens TO voting_admin;
-- GRANT SELECT, INSERT ON voter_identities TO voting_admin;
