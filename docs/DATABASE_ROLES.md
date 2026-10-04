# Database Roles and Privileges

To ensure least privilege and proper separation of concerns, the IPD Voting database requires three separate PostgreSQL roles. **Do not use a superuser account for normal application access.**

_Note: The following role grants are instructional procedures and must be explicitly executed and verified by a DBA in the target environment._

## 1. Migration Role (`voting_migrator`)
**Purpose:** Executes DDL statements (schema migrations) and creates tables/indexes.
**Privileges:**
- `CREATE` on the database/schema.
- Ownership of all tables during migration.
```sql
CREATE ROLE voting_migrator WITH LOGIN PASSWORD '<REPLACE_WITH_SECURE_PASSWORD>';
GRANT CREATE ON DATABASE ipd_voting_prod TO voting_migrator;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO voting_migrator;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO voting_migrator;
ALTER DEFAULT PRIVILEGES FOR ROLE voting_migrator IN SCHEMA public GRANT ALL ON TABLES TO voting_migrator;
ALTER DEFAULT PRIVILEGES FOR ROLE voting_migrator IN SCHEMA public GRANT ALL ON SEQUENCES TO voting_migrator;
```

## 2. Runtime Application Role (`voting_app`)
**Purpose:** Used by the Python `VotingEngine` for standard runtime data access.
**Privileges:**
- `SELECT`, `INSERT`, `UPDATE` on operational tables.
- **Restrictions:** Cannot `DROP` tables, `ALTER` schema, or modify migration ledger tables (`schema_migrations`). Cannot `DELETE` arbitrary tables.

**Setup Sequence:**
Follow these steps in order to ensure permissions are applied correctly:

### Step 1: Create the Role
Create the `voting_app` role first:
```sql
CREATE ROLE voting_app WITH LOGIN PASSWORD '<REPLACE_WITH_SECURE_PASSWORD>';
```

### Step 2: Apply Migrations
Apply all schema migrations (including `004_voter_identities.sql`).
*Note: Migration 004 dynamically grants `SELECT` on `voter_identities` if the `voting_app` role already exists.*

### Step 3: Apply Post-Migration Grants
After all migrations are complete, apply the runtime-role grants to the existing tables. This includes an explicit `GRANT SELECT` and `REVOKE INSERT, UPDATE, DELETE` on `voter_identities`. The runtime role must not be able to change identity mappings.

*If the `voting_app` role is created after migration 004, this post-migration grant step is required to safely restrict permissions.*
```sql
-- Apply baseline operational grants
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO voting_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO voting_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE ON TABLES TO voting_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO voting_app;

-- Restrict sensitive schema and migration structures
REVOKE CREATE ON SCHEMA public FROM voting_app;
REVOKE ALL PRIVILEGES ON TABLE schema_migrations FROM voting_app;
GRANT SELECT ON TABLE schema_migrations TO voting_app;

-- Restrict identity mappings (Runtime must only read mappings, never write them)
GRANT SELECT ON TABLE voter_identities TO voting_app;
REVOKE INSERT, UPDATE, DELETE ON TABLE voter_identities FROM voting_app;

-- Restrict provisioning tokens (Runtime must not read or write tokens)
REVOKE ALL PRIVILEGES ON TABLE provisioning_tokens FROM voting_app;
```

## 5. Provisioning Admin Role (`voting_admin`)
**Purpose:** Executes out-of-band single-use identity provisioning and token issuance. Not connected to `api.py` or the runtime application.
**Privileges:**
- Read-only access to `voters`.
- `SELECT`, `INSERT`, `UPDATE` on `provisioning_tokens`.
- `SELECT`, `INSERT` on `voter_identities`.
- No access to ballots, ledgers, or session data.
```sql
CREATE ROLE voting_admin WITH LOGIN PASSWORD '<REPLACE_WITH_SECURE_PASSWORD>';
GRANT CONNECT ON DATABASE ipd_voting_prod TO voting_admin;
GRANT USAGE ON SCHEMA public TO voting_admin;
GRANT SELECT ON voters TO voting_admin;
GRANT SELECT, INSERT, UPDATE ON provisioning_tokens TO voting_admin;
GRANT SELECT, INSERT ON voter_identities TO voting_admin;
```

## 3. Retention Role (`voting_retention`)
**Purpose:** Executes automated scheduled pruning of retained data.
**Privileges:**
- `CONNECT` to database and `USAGE` on schema.
- `SELECT`, `UPDATE (session_id)` on `voting_sessions`. (This allows `FOR SHARE` locking without granting the ability to update sensitive fields like `legal_hold` or `certified_at`).
- `SELECT`, `DELETE` on tables subject to the retention policy (`session_voters`, `voter_participation`, `ballots`, `audit_ledger`, `security_audit_log`).
```sql
CREATE ROLE voting_retention WITH LOGIN PASSWORD '<REPLACE_WITH_SECURE_PASSWORD>';
GRANT CONNECT ON DATABASE ipd_voting_prod TO voting_retention;
GRANT USAGE ON SCHEMA public TO voting_retention;
GRANT SELECT, UPDATE (session_id) ON TABLE voting_sessions TO voting_retention;
GRANT SELECT, DELETE ON TABLE session_voters, voter_participation, ballots, audit_ledger, security_audit_log TO voting_retention;
```

## 4. Backup and Restore Role (`voting_backup`)
**Purpose:** Executes physical and logical backups.
**Privileges:**
- Read-only access (`SELECT`) to all tables, including future tables.
- Cannot insert, update, or delete any data.
```sql
CREATE ROLE voting_backup WITH LOGIN PASSWORD '<REPLACE_WITH_SECURE_PASSWORD>';
GRANT CONNECT ON DATABASE ipd_voting_prod TO voting_backup;
GRANT USAGE ON SCHEMA public TO voting_backup;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO voting_backup;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO voting_backup;
```
