# Backup and Restore Procedures

Data retention and recovery require routine rolling backups. All backups must be encrypted at rest because they contain anonymized ballot payloads and voter participation timestamps.

## Rolling Backup Policy
- **Frequency**: Daily full backup, with Write-Ahead Logging (WAL) archiving for point-in-time recovery.
- **Retention**: Backups are retained for a rolling window of **35 days**, ensuring that deleted/pruned data (such as PII from finalized elections) ages out of the backups completely to comply with the primary data retention policy.

## Deployment Tasks
Note: Scheduled backups, WAL archiving, encryption-key management, and automated scheduled pruning are **deployment tasks** that must be managed by the operations environment. This repository provides the schema and data access layer, but does not automate these cron jobs.

## Execution (Encrypted Dump)
Use the `voting_backup` role to extract data and immediately pipe it to an encryption tool (e.g., GPG) before writing to the storage medium.

```bash
pg_dump -U voting_backup -h localhost -d ipd_voting_prod -F c | \
  gpg --encrypt --recipient backup-key@example.com > /backups/voting_prod_$(date +%F).dump.gpg
```

## Restore Procedure (Disaster Recovery & Verification)
Never restore directly into `ipd_voting_prod` or `ipd_voting_dev` without explicit DBA authorization. To verify backups, always restore into a separate, disposable isolated database (`test_ipd_restore_verify`).

### 1. Decrypt the Backup
```bash
gpg --decrypt /backups/voting_prod_202X-XX-XX.dump.gpg > /tmp/restore.dump
```

### 2. Restore into Disposable Verification Environment
```bash
# Ensure the test database is clean
dropdb -U postgres test_ipd_restore_verify --if-exists
createdb -U postgres test_ipd_restore_verify

# Restore the dump
pg_restore -U postgres -d test_ipd_restore_verify -1 /tmp/restore.dump
```

### 3. Verification
Verify the restored schema and expected row counts directly via SQL. Do not run `voting.test_postgres_db` against this restored database because the test suite drops and recreates tables.

```bash
psql -U postgres -d test_ipd_restore_verify -c "SELECT count(*) FROM voting_sessions;"
psql -U postgres -d test_ipd_restore_verify -c "SELECT count(*) FROM ballots;"
```
