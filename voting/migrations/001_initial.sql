CREATE TABLE IF NOT EXISTS voters (
    voter_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    department TEXT NOT NULL,
    role TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS candidates (
    candidate_id TEXT PRIMARY KEY,
    candidate_name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ballot_options (
    option_id TEXT PRIMARY KEY,
    option_label TEXT NOT NULL,
    option_type TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS voting_sessions (
    session_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    question TEXT,
    session_type TEXT NOT NULL,
    start_time TIMESTAMP WITH TIME ZONE NOT NULL,
    end_time TIMESTAMP WITH TIME ZONE NOT NULL,
    status TEXT NOT NULL,
    UNIQUE (session_id, session_type)
);

CREATE TABLE IF NOT EXISTS session_voters (
    session_id TEXT NOT NULL,
    voter_id TEXT NOT NULL,
    PRIMARY KEY (session_id, voter_id),
    FOREIGN KEY (session_id) REFERENCES voting_sessions(session_id),
    FOREIGN KEY (voter_id) REFERENCES voters(voter_id)
);

CREATE TABLE IF NOT EXISTS session_choices (
    session_choice_id UUID PRIMARY KEY,
    session_id TEXT NOT NULL,
    session_type TEXT NOT NULL,
    candidate_id TEXT,
    option_id TEXT,
    FOREIGN KEY (session_id, session_type) REFERENCES voting_sessions(session_id, session_type),
    FOREIGN KEY (candidate_id) REFERENCES candidates(candidate_id),
    FOREIGN KEY (option_id) REFERENCES ballot_options(option_id),
    CHECK (
        (session_type = 'candidate_election' AND candidate_id IS NOT NULL AND option_id IS NULL) OR 
        (session_type IN ('yes_no', 'single_choice') AND option_id IS NOT NULL AND candidate_id IS NULL)
    )
);

CREATE TABLE IF NOT EXISTS voter_participation (
    session_id TEXT NOT NULL,
    voter_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('PENDING', 'COMMITTED')),
    reservation_token UUID,
    idempotency_key UUID NOT NULL,
    reserved_at TIMESTAMP WITH TIME ZONE NOT NULL,
    receipt_id UUID,
    PRIMARY KEY (session_id, voter_id),
    FOREIGN KEY (session_id) REFERENCES voting_sessions(session_id),
    FOREIGN KEY (voter_id) REFERENCES voters(voter_id)
);

CREATE TABLE IF NOT EXISTS ballots (
    ballot_id SERIAL PRIMARY KEY,
    session_id TEXT NOT NULL,
    encrypted_vote_payload TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES voting_sessions(session_id)
);

CREATE TABLE IF NOT EXISTS audit_ledger (
    session_id TEXT NOT NULL,
    block_index INTEGER NOT NULL,
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
    encrypted_vote_payload TEXT NOT NULL,
    PRIMARY KEY (session_id, block_index),
    FOREIGN KEY (session_id) REFERENCES voting_sessions(session_id)
);
