"""
voting_engine.py
M2/M3: Multi-Voter Voting Engine with Quantum & Blockchain Pipeline Integration.

Prototype Scope & Architecture Disclaimers:
- Single-Process Prototype: All state (voter reservations, voter participation sets,
  ephemeral BB84 keystore, vote metadata, aggregate tallies, and session blockchains)
  is held strictly in local process memory.
- No Durability Across Restarts: State does NOT survive application process restarts
  and does NOT coordinate across multiple server instances or processes.
- Voter Linkability Note: The prototype does not expose plaintext vote choices in receipts
  or log files. However, each block on the blockchain explicitly records voter_id alongside
  the encrypted vote payload (Block.vote_data['voter_id']), and the receipt pairs voter_id
  with block_index. Consequently, the stored ciphertext is directly linked to the voter's identity
  on the ledger. This prototype does NOT claim anonymous or unlinkable ballot privacy.

Key capabilities & security guarantees:
- Enforces M1 schema integrity rules across candidate_election, yes_no, and single_choice sessions.
- Validates CSV data loading, catching duplicate primary keys and duplicate junction records.
- Accepts exactly one choice argument per vote (choice_id, candidate_id, or option_id); rejects none or multiples.
- Thread-safe atomic voter reservation and serialized blockchain commits prevent race-condition duplicates.
- Releases voter reservation if BB84 quantum negotiation or any downstream step fails.
- Commits are atomic: if metadata or tally updates fail during commit, the newly appended block is rolled back.
- Binds vote_id to session_id and block_index in engine metadata for strict verification.
- Explicit verification semantics: returns decryptable=True and verified=False when expected_choice is omitted.
- Fully integrated with Quantum encoding (vol1), secure BB84 key exchange (vol2),
  PQC AES encryption (vol3), and tamper-evident session blockchain (vol4).
"""

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import threading
from typing import Any, Dict, List, Optional, Set, Tuple

# Ensure project root is in sys.path so volume modules can be imported
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from voting.ballot_encoding import encode_choice, prepare_circuit, M3State
from vol2_bb84 import run_secure_bb84
from vol3_pqc import encrypt_vote, decrypt_vote
from vol4_blockchain import Blockchain, Block


class VotingError(ValueError):
    """Base exception for voting engine errors (subclasses ValueError)."""
    pass


class UnknownSessionError(VotingError):
    """Raised when session_id does not exist."""
    pass


class InactiveSessionError(VotingError):
    """Raised when session is not in ACTIVE status or current time is outside the session window."""
    pass


class UnknownVoterError(VotingError):
    """Raised when voter_id does not exist."""
    pass


class IneligibleVoterError(VotingError):
    """Raised when voter is not registered/eligible for the session."""
    pass


class UnknownCandidateError(VotingError):
    """Raised when candidate_id does not exist."""
    pass


class CandidateNotAssignedError(VotingError):
    """Raised when candidate is not assigned to the session."""
    pass


class InvalidChoiceError(VotingError):
    """Raised when choice type doesn't match session type (e.g. candidate in decision session, or option in candidate election)."""
    pass


class UnknownOptionError(InvalidChoiceError):
    """Raised when option_id does not exist."""
    pass


class OptionNotAssignedError(InvalidChoiceError):
    """Raised when option is not assigned to the session."""
    pass


class DuplicateVoteError(VotingError):
    """Raised when a voter attempts to vote more than once in the same session."""
    pass


class BB84SecurityError(VotingError):
    """Raised when BB84 quantum key distribution fails security checks or aborts."""
    pass


class VotingEngine:
    """
    Multi-voter, multi-session voting engine with quantum-safe blockchain integration.

    Prototype Scope & In-Memory State Disclaimers:
    ---------------------------------------------
    1. Single-Process In-Memory State:
       The session blockchains, keystore, voter participation sets, and aggregate tallies
       are maintained strictly in process memory. They are NOT durable across process restarts
       and do NOT coordinate across multiple application processes or distributed workers.
    2. Linkability & Ledger Privacy:
       Plaintext voter-to-choice records are never stored to disk or kept in engine state.
       However, each block in the blockchain explicitly records voter_id alongside the encrypted
       vote (Block.vote_data['voter_id']), and the vote receipt links voter_id to block_index.
       Therefore, the blockchain currently stores voter-linked ciphertext; this prototype does
       NOT provide anonymous or unlinkable ballot privacy.
    """

    VALID_STATUSES = {"UPCOMING", "ACTIVE", "COMPLETED", "CLOSED", "ARCHIVED"}
    VALID_SESSION_TYPES = {"candidate_election", "yes_no", "single_choice"}

    def __init__(
        self,
        data_dir: Optional[Path | str] = None,
        auto_load: bool = True,
        reference_time: Optional[datetime] = None,
        use_postgres: Optional[bool] = None,
    ):
        import os
        if use_postgres is None:
            self.use_postgres = not bool(os.environ.get("USE_MEMORY_STORAGE"))
        else:
            self.use_postgres = use_postgres

        self.repo = None
        if self.use_postgres:
            db_url = os.environ.get("DATABASE_URL")
            if not db_url:
                raise ValueError("DATABASE_URL environment variable is required when PostgreSQL is selected. To use memory storage, set USE_MEMORY_STORAGE=1.")
            from voting.postgres_db import PostgresVotingRepository
            self.repo = PostgresVotingRepository(db_url)

        if data_dir is None:
            self.data_dir = Path(__file__).resolve().parent.parent / "data"
        else:
            self.data_dir = Path(data_dir)

        self.reference_time: Optional[datetime] = reference_time

        # Loaded reference datasets from M1 CSVs
        self.voters: Dict[str, Dict[str, str]] = {}
        self.candidates: Dict[str, Dict[str, str]] = {}
        self.ballot_options: Dict[str, Dict[str, str]] = {}
        self.sessions: Dict[str, Dict[str, str]] = {}
        self.session_voters: Dict[str, Set[str]] = {}
        self.session_candidates: Dict[str, Set[str]] = {}
        self.session_options: Dict[str, Set[str]] = {}

        # M3: Blockchain storage per session (maintains session isolation)
        self.session_chains: Dict[str, Blockchain] = {}

        # Concurrency & Participation State
        # - _lock: guards reservation, state updates, and blockchain writes
        # - _reserved_voters: tracks (session_id, voter_id) currently in-flight
        # - _voted_voters: tracks (session_id, voter_id) that have successfully committed
        self._lock = threading.RLock()
        self._reserved_voters: Set[Tuple[str, str]] = set()
        self._voted_voters: Set[Tuple[str, str]] = set()

        # Privacy-Preserving Aggregate Tallies:
        # self._tallies[session_id][choice_id] -> count
        # No per-voter plaintext choice association is retained.
        self._tallies: Dict[str, Dict[str, int]] = {}
        self._total_votes: Dict[str, int] = {}
        self._vote_counter: int = 0

        # Prototype-only in-memory vote metadata & keystore:
        # - _prototype_keystore: Maps vote_id -> bb84_key (list[int]).
        # - _prototype_vote_metadata: Maps vote_id -> {"session_id": str, "block_index": int, "voter_id": str}.
        # - _prototype_vote_blocks: Maps vote_id -> block_index (int) for backward compatibility.
        self._prototype_keystore: Dict[str, List[int]] = {}
        self._prototype_vote_metadata: Dict[str, Dict[str, Any]] = {}
        self._prototype_vote_blocks: Dict[str, int] = {}

        if auto_load and not self.use_postgres:
            self.load_data()
            self.validate_data()

    def get_now(self) -> datetime:
        """Returns the current reference time (or real UTC now if not set)."""
        if self.reference_time is not None:
            return self.reference_time
        return datetime.now(timezone.utc)

    def set_reference_time(self, ref: Optional[datetime | str]) -> None:
        """Sets an explicit reference time (or None to use real UTC now)."""
        if ref is None:
            self.reference_time = None
        elif isinstance(ref, str):
            self.reference_time = datetime.fromisoformat(ref.replace("Z", "+00:00"))
        elif isinstance(ref, datetime):
            self.reference_time = ref if ref.tzinfo else ref.replace(tzinfo=timezone.utc)

    def load_data(self, data_dir: Optional[Path | str] = None) -> None:
        """
        Loads voters, candidates, ballot options, sessions, and junction mappings from M1 CSVs.
        Enforces strict uniqueness checks on primary keys and junction mapping pairs.
        """
        target_dir = Path(data_dir) if data_dir is not None else self.data_dir

        voters_file = target_dir / "voters.csv"
        candidates_file = target_dir / "candidates.csv"
        ballot_options_file = target_dir / "ballot_options.csv"
        sessions_file = target_dir / "voting_sessions.csv"
        session_voters_file = target_dir / "session_voters.csv"
        session_candidates_file = target_dir / "session_candidates.csv"
        session_options_file = target_dir / "session_options.csv"

        for file_path in [
            voters_file,
            candidates_file,
            ballot_options_file,
            sessions_file,
            session_voters_file,
            session_candidates_file,
            session_options_file,
        ]:
            if not file_path.is_file():
                raise FileNotFoundError(f"Required M1 data file not found: {file_path}")

        duplicate_errors = []

        # 1. Voters (Unique voter_id check)
        self.voters = {}
        seen_voters = set()
        with open(voters_file, mode="r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for idx, row in enumerate(reader, start=2):
                vid = row["voter_id"].strip()
                if vid in seen_voters:
                    duplicate_errors.append(f"voters.csv: duplicate voter_id '{vid}' at row {idx}.")
                seen_voters.add(vid)
                self.voters[vid] = {k: v.strip() for k, v in row.items()}

        # 2. Candidates (Unique candidate_id check)
        self.candidates = {}
        seen_candidates = set()
        with open(candidates_file, mode="r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for idx, row in enumerate(reader, start=2):
                cid = row["candidate_id"].strip()
                if cid in seen_candidates:
                    duplicate_errors.append(f"candidates.csv: duplicate candidate_id '{cid}' at row {idx}.")
                seen_candidates.add(cid)
                self.candidates[cid] = {k: v.strip() for k, v in row.items()}

        # 3. Ballot Options (Unique option_id check)
        self.ballot_options = {}
        seen_options = set()
        with open(ballot_options_file, mode="r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for idx, row in enumerate(reader, start=2):
                oid = row["option_id"].strip()
                if oid in seen_options:
                    duplicate_errors.append(f"ballot_options.csv: duplicate option_id '{oid}' at row {idx}.")
                seen_options.add(oid)
                self.ballot_options[oid] = {k: v.strip() for k, v in row.items()}

        # 4. Sessions (Unique session_id check)
        self.sessions = {}
        seen_sessions = set()
        with open(sessions_file, mode="r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for idx, row in enumerate(reader, start=2):
                sid = row["session_id"].strip()
                if sid in seen_sessions:
                    duplicate_errors.append(f"voting_sessions.csv: duplicate session_id '{sid}' at row {idx}.")
                seen_sessions.add(sid)
                self.sessions[sid] = {k: v.strip() for k, v in row.items()}

        # 5. Session Voters (Unique (session_id, voter_id) check)
        self.session_voters = {sid: set() for sid in self.sessions}
        seen_sv = set()
        with open(session_voters_file, mode="r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for idx, row in enumerate(reader, start=2):
                sid = row["session_id"].strip()
                vid = row["voter_id"].strip()
                pair = (sid, vid)
                if pair in seen_sv:
                    duplicate_errors.append(f"session_voters.csv: duplicate pair ('{sid}', '{vid}') at row {idx}.")
                seen_sv.add(pair)
                if sid not in self.session_voters:
                    self.session_voters[sid] = set()
                self.session_voters[sid].add(vid)

        # 6. Session Candidates (Unique (session_id, candidate_id) check)
        self.session_candidates = {sid: set() for sid in self.sessions}
        seen_sc = set()
        with open(session_candidates_file, mode="r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for idx, row in enumerate(reader, start=2):
                sid = row["session_id"].strip()
                cid = row["candidate_id"].strip()
                pair = (sid, cid)
                if pair in seen_sc:
                    duplicate_errors.append(f"session_candidates.csv: duplicate pair ('{sid}', '{cid}') at row {idx}.")
                seen_sc.add(pair)
                if sid not in self.session_candidates:
                    self.session_candidates[sid] = set()
                self.session_candidates[sid].add(cid)

        # 7. Session Options (Unique (session_id, option_id) check)
        self.session_options = {sid: set() for sid in self.sessions}
        seen_so = set()
        with open(session_options_file, mode="r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for idx, row in enumerate(reader, start=2):
                sid = row["session_id"].strip()
                oid = row["option_id"].strip()
                pair = (sid, oid)
                if pair in seen_so:
                    duplicate_errors.append(f"session_options.csv: duplicate pair ('{sid}', '{oid}') at row {idx}.")
                seen_so.add(pair)
                if sid not in self.session_options:
                    self.session_options[sid] = set()
                self.session_options[sid].add(oid)

        if duplicate_errors:
            raise VotingError("Data validation failed (duplicate records):\n" + "\n".join(duplicate_errors))

        # 8. Initialize blockchains and aggregate tallies
        self.session_chains = {sid: Blockchain() for sid in self.sessions}
        self._init_tallies()

    def _init_tallies(self) -> None:
        """Initializes empty tallies for all assigned choices per session."""
        self._tallies = {}
        self._total_votes = {}
        for sid, sess in self.sessions.items():
            stype = sess.get("session_type", "")
            if stype == "candidate_election":
                self._tallies[sid] = {cid: 0 for cid in sorted(self.session_candidates.get(sid, set()))}
            else:
                self._tallies[sid] = {oid: 0 for oid in sorted(self.session_options.get(sid, set()))}
            self._total_votes[sid] = 0

    def validate_data(self) -> None:
        """Performs consistency and integrity checks on loaded M1 data."""
        errors = []

        # Validate sessions status and session_type
        for sid, sess in self.sessions.items():
            status = sess.get("status")
            if status not in self.VALID_STATUSES:
                errors.append(f"Session {sid} has invalid status '{status}'.")
            stype = sess.get("session_type")
            if stype not in self.VALID_SESSION_TYPES:
                errors.append(f"Session {sid} has invalid session_type '{stype}'.")

        # Validate session_voters foreign keys
        for sid, v_set in self.session_voters.items():
            if sid not in self.sessions:
                errors.append(f"session_voters references unknown session '{sid}'.")
            for vid in v_set:
                if vid not in self.voters:
                    errors.append(f"session_voters references unknown voter '{vid}'.")

        # Validate session_candidates foreign keys
        for sid, c_set in self.session_candidates.items():
            if sid not in self.sessions:
                errors.append(f"session_candidates references unknown session '{sid}'.")
            for cid in c_set:
                if cid not in self.candidates:
                    errors.append(f"session_candidates references unknown candidate '{cid}'.")

        # Validate session_options foreign keys
        for sid, o_set in self.session_options.items():
            if sid not in self.sessions:
                errors.append(f"session_options references unknown session '{sid}'.")
            for oid in o_set:
                if oid not in self.ballot_options:
                    errors.append(f"session_options references unknown option '{oid}'.")

        # Validate session-type specific rules
        for sid, sess in self.sessions.items():
            stype = sess.get("session_type", "")
            assigned_cands = self.session_candidates.get(sid, set())
            assigned_opts = self.session_options.get(sid, set())

            if stype == "candidate_election":
                if len(assigned_cands) < 2:
                    errors.append(
                        f"Session {sid} (candidate_election) requires >= 2 assigned candidates (found {len(assigned_cands)})."
                    )
                if len(assigned_opts) > 0:
                    errors.append(
                        f"Session {sid} (candidate_election) must not have assigned ballot options (found {len(assigned_opts)})."
                    )

            elif stype == "yes_no":
                if len(assigned_cands) > 0:
                    errors.append(
                        f"Session {sid} (yes_no) must not have assigned candidates (found {len(assigned_cands)})."
                    )
                # Option types: only yes_no and abstain permitted
                for oid in assigned_opts:
                    opt = self.ballot_options.get(oid, {})
                    otype = opt.get("option_type")
                    if otype not in {"yes_no", "abstain"}:
                        errors.append(
                            f"Session {sid} (yes_no) has option '{oid}' of disallowed type '{otype}'; "
                            "only 'yes_no' and 'abstain' allowed."
                        )
                # Require Yes and No options of type yes_no
                yes_no_labels = {
                    self.ballot_options[oid]["option_label"].strip().lower()
                    for oid in assigned_opts
                    if oid in self.ballot_options and self.ballot_options[oid].get("option_type") == "yes_no"
                }
                if "yes" not in yes_no_labels or "no" not in yes_no_labels:
                    errors.append(
                        f"Session {sid} (yes_no) must have both Yes and No options of type 'yes_no'."
                    )

            elif stype == "single_choice":
                if len(assigned_cands) > 0:
                    errors.append(
                        f"Session {sid} (single_choice) must not have assigned candidates (found {len(assigned_cands)})."
                    )
                for oid in assigned_opts:
                    opt = self.ballot_options.get(oid, {})
                    if opt.get("option_type") == "yes_no":
                        errors.append(
                            f"Session {sid} (single_choice) cannot contain option '{oid}' of type 'yes_no'."
                        )
                non_yes_no = [
                    oid for oid in assigned_opts
                    if self.ballot_options.get(oid, {}).get("option_type") != "yes_no"
                ]
                if len(non_yes_no) < 2:
                    errors.append(
                        f"Session {sid} (single_choice) requires >= 2 non-yes/no options (found {len(non_yes_no)})."
                    )

        if errors:
            raise VotingError("Data validation failed:\n" + "\n".join(errors))

    def get_session_chain(self, session_id: str) -> Blockchain:
        """Returns the Blockchain instance for the given session_id, creating one if needed."""
        sid = str(session_id).strip()
        with self._lock:
            if sid not in self.session_chains:
                self.session_chains[sid] = Blockchain()
            return self.session_chains[sid]

    def get_session_choices(self, session_id: str) -> List[str]:
        """
        Returns the sorted list of valid choice IDs assigned to the session.
        - For candidate_election: returns candidate_ids (people candidates only).
        - For yes_no and single_choice: returns option_ids.
        Raises UnknownSessionError if session_id does not exist.
        """
        sid = str(session_id).strip()
        if self.use_postgres:
            choices = self.repo.get_session_choices(sid)
            if choices is None:
                raise UnknownSessionError(f"Session '{sid}' not found.")
            return choices
        with self._lock:
            if sid not in self.sessions:
                raise UnknownSessionError(f"Session '{sid}' not found.")
            stype = self.sessions[sid].get("session_type", "")
            if stype == "candidate_election":
                return sorted(list(self.session_candidates.get(sid, set())))
            elif stype in ("yes_no", "single_choice"):
                return sorted(list(self.session_options.get(sid, set())))
            else:
                return []

    def cast_vote(
        self,
        session_id: str,
        voter_id: str,
        choice_id: Optional[str] = None,
        candidate_id: Optional[str] = None,
        option_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Executes the complete M3 Quantum & Blockchain voting pipeline across all session types.

        Requirement Rules:
        - Exactly one of choice_id, candidate_id, or option_id must be supplied.
        - candidate_election accepts candidate_id assigned to session.
        - yes_no and single_choice accept option_id assigned to session.
        - Concurrency-safe: reserves (session_id, voter_id) atomically before BB84; releases on abort.
        - Privacy-safe: does NOT emit or retain plaintext choice associations.
        """
        session_id = str(session_id).strip()
        voter_id = str(voter_id).strip()

        # ── Step 1: Strict Choice Parameter Validation ───────────────────────
        supplied = [
            ("choice_id", choice_id),
            ("candidate_id", candidate_id),
            ("option_id", option_id),
        ]
        non_none = [(name, val) for name, val in supplied if val is not None]

        if len(non_none) == 0:
            raise VotingError(
                "No vote choice supplied. Exactly one of 'choice_id', 'candidate_id', or 'option_id' must be provided."
            )
        if len(non_none) > 1:
            names = [name for name, _ in non_none]
            raise VotingError(
                f"Multiple vote choices supplied ({', '.join(names)}). "
                "Exactly one of 'choice_id', 'candidate_id', or 'option_id' must be provided."
            )

        param_name, raw_choice = non_none[0]
        choice = str(raw_choice).strip()

        if self.use_postgres:
            if not getattr(self, 'repo', None):
                raise VotingError("PostgreSQL repository not initialized.")
            if not idempotency_key:
                import uuid
                idempotency_key = str(uuid.uuid4())
            if not self.repo.validate_choice(session_id, choice):
                raise InvalidChoiceError(f"Choice not valid for session.")
            success, reservation_token, existing_receipt = self.repo.reserve_vote(session_id, voter_id, idempotency_key)
            if not success:
                return {"status": "success", "message": "Idempotent response.", "receipt": existing_receipt}

            try:
                # 3.1 Quantum Vote Encoding (M3-v1)
                session_choices = self.repo.get_session_choices(session_id)
                encoded_result = encode_choice(choice_ids=session_choices, selected_choice_id=choice)

                # Note: The Qiskit circuit is a simulated, prepared M3 artifact with no downstream consumer
                # in the current pipeline; the encoded bits are the current handoff.
                circuit = prepare_circuit(encoded_result["encoded_bits"], encoded_result["choice_count"])
                m3_state = M3State(
                    encoding_version=encoded_result["encoding_version"],
                    choice_count=encoded_result["choice_count"],
                    qubit_count=encoded_result["qubit_count"],
                    encoded_bits=encoded_result["encoded_bits"],
                    circuit=circuit
                )

                # 3.2 BB84 Quantum Key Distribution (vol2) - M4 Security Gate
                bb84_result = run_secure_bb84(min_key_length=256)
                if not bb84_result.get("secure", False) or bb84_result.get("aborted", True):
                    reason = bb84_result.get("reason", "unknown")
                    raise BB84SecurityError(f"BB84 security check failed: {reason}")
                bb84_key = bb84_result["final_key"]
                if len(bb84_key) < 256:
                    raise BB84SecurityError("BB84 security check failed: undersized or invalid key.")

                # 3.3 Post-Quantum Encryption (vol3)
                ciphertext_bytes = encrypt_vote(vote_data=m3_state.encoded_bits, bb84_key=bb84_key)
                ciphertext_hex = ciphertext_bytes.hex()
            except Exception as e:
                # Since postgres handles the reservation state in a database transaction,
                # a failure here means the reservation will eventually expire.
                raise VotingError(f"Cryptographic pipeline failed: {str(e)}")

            receipt = self.repo.finalize_vote(session_id, voter_id, reservation_token, ciphertext_hex)
            return {"status": "success", "message": "Vote cast and recorded on PostgreSQL.", "receipt": receipt, "num_qubits": m3_state.qubit_count}

        # ── Step 2: Atomic Reservation & Pre-Execution Validation ────────────
        with self._lock:
            # 2.1 Session existence
            if session_id not in self.sessions:
                raise UnknownSessionError(f"Session '{session_id}' not found.")

            session = self.sessions[session_id]
            session_type = session.get("session_type", "")

            # 2.2 Session ACTIVE status
            status = session.get("status", "").upper()
            if status != "ACTIVE":
                raise InactiveSessionError(
                    f"Session '{session_id}' is not ACTIVE (current status: '{session.get('status')}')."
                )

            # 2.3 Session time window consistency
            start_str = session.get("start_time")
            end_str = session.get("end_time")
            if start_str and end_str:
                try:
                    start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                    end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                    now = self.get_now()
                    if now < start_dt:
                        raise InactiveSessionError(
                            f"Session '{session_id}' voting window has not started yet "
                            f"(start_time={start_str}, current_time={now.strftime('%Y-%m-%dT%H:%M:%SZ')})."
                        )
                    if now >= end_dt:
                        raise InactiveSessionError(
                            f"Session '{session_id}' voting window has already ended "
                            f"(end_time={end_str}, current_time={now.strftime('%Y-%m-%dT%H:%M:%SZ')})."
                        )
                except ValueError as ex:
                    if isinstance(ex, InactiveSessionError):
                        raise
                    raise VotingError(f"Invalid timestamp in session '{session_id}': {ex}")

            # 2.4 Voter existence
            if voter_id not in self.voters:
                raise UnknownVoterError(f"Voter '{voter_id}' not found.")

            # 2.5 Voter eligibility
            eligible_voters = self.session_voters.get(session_id, set())
            if voter_id not in eligible_voters:
                raise IneligibleVoterError(
                    f"Voter '{voter_id}' is not eligible for session '{session_id}'."
                )

            # 2.6 Duplicate check (both committed and in-flight reservation)
            participation_key = (session_id, voter_id)
            if participation_key in self._voted_voters or participation_key in self._reserved_voters:
                raise DuplicateVoteError(
                    f"Duplicate vote rejected: Voter '{voter_id}' has already voted or is in-progress in session '{session_id}'."
                )

            session_chain = self.get_session_chain(session_id)
            if session_chain.has_voter_voted(voter_id):
                raise DuplicateVoteError(
                    f"Duplicate vote rejected on blockchain: Voter '{voter_id}' already has a committed block in session '{session_id}'."
                )

            # 2.7 Choice type & assignment validation per session_type
            if session_type == "candidate_election":
                if param_name == "option_id":
                    raise InvalidChoiceError(
                        f"'option_id' parameter cannot be used for candidate_election session '{session_id}'."
                    )
                if choice in self.ballot_options or (choice.startswith("O") and choice not in self.candidates):
                    raise InvalidChoiceError(
                        f"Option ID '{choice}' cannot be voted on in candidate_election session '{session_id}'."
                    )
                if choice not in self.candidates:
                    raise UnknownCandidateError(f"Candidate '{choice}' not found.")

                assigned_candidates = self.session_candidates.get(session_id, set())
                if choice not in assigned_candidates:
                    raise CandidateNotAssignedError(
                        f"Candidate '{choice}' is not assigned to session '{session_id}'."
                    )
                choices_pool = sorted(list(assigned_candidates))

            elif session_type in ("yes_no", "single_choice"):
                if param_name == "candidate_id":
                    raise InvalidChoiceError(
                        f"'candidate_id' parameter cannot be used for {session_type} session '{session_id}'."
                    )
                if choice in self.candidates or (choice.startswith("C") and choice not in self.ballot_options):
                    raise InvalidChoiceError(
                        f"Candidate ID '{choice}' cannot be voted on in {session_type} session '{session_id}'."
                    )
                if choice not in self.ballot_options:
                    raise UnknownOptionError(f"Option '{choice}' not found.")

                assigned_options = self.session_options.get(session_id, set())
                if choice not in assigned_options:
                    raise OptionNotAssignedError(
                        f"Option '{choice}' is not assigned to session '{session_id}'."
                    )
                choices_pool = sorted(list(assigned_options))

            else:
                raise VotingError(f"Unsupported session_type '{session_type}' for session '{session_id}'.")

            # Atomically reserve voter to block concurrent race conditions
            self._reserved_voters.add(participation_key)

        # ── Step 3: Expensive Security Pipeline (outside lock) ───────────────
        try:
            # 3.1 Quantum Vote Encoding (M3-v1)
            encoded_result = encode_choice(choice_ids=choices_pool, selected_choice_id=choice)

            # Note: The Qiskit circuit is a simulated, prepared M3 artifact with no downstream consumer
            # in the current pipeline; the encoded bits are the current handoff.
            circuit = prepare_circuit(encoded_result["encoded_bits"], encoded_result["choice_count"])
            m3_state = M3State(
                encoding_version=encoded_result["encoding_version"],
                choice_count=encoded_result["choice_count"],
                qubit_count=encoded_result["qubit_count"],
                encoded_bits=encoded_result["encoded_bits"],
                circuit=circuit
            )

            # 3.2 BB84 Quantum Key Distribution (vol2) - M4 Security Gate
            bb84_result = run_secure_bb84(min_key_length=256)
            if not bb84_result.get("secure", False) or bb84_result.get("aborted", True):
                reason = bb84_result.get("reason", "unknown")
                raise BB84SecurityError(f"BB84 security check failed: {reason}")

            bb84_key = bb84_result["final_key"]
            if len(bb84_key) < 256:
                raise BB84SecurityError("BB84 security check failed: undersized or invalid key.")

            # 3.3 Post-Quantum Encryption (vol3)
            ciphertext_bytes = encrypt_vote(vote_data=m3_state.encoded_bits, bb84_key=bb84_key)
            ciphertext_hex = ciphertext_bytes.hex()

        except Exception:
            # Release atomic reservation if BB84 or encryption fails so voter can retry
            with self._lock:
                self._reserved_voters.discard(participation_key)
            raise

        # ── Step 4: Serialized Blockchain Commit & Metadata Update ───────────
        with self._lock:
            # Snapshot complete pre-commit state for transactional rollback
            initial_chain_len = len(session_chain.chain)
            initial_keystore = {k: list(v) for k, v in self._prototype_keystore.items()}
            initial_metadata = {k: dict(v) for k, v in self._prototype_vote_metadata.items()}
            initial_vote_blocks = dict(self._prototype_vote_blocks)
            initial_tallies = {sid: dict(counts) for sid, counts in self._tallies.items()}
            initial_total_votes = dict(self._total_votes)
            initial_vote_counter = self._vote_counter
            initial_voted_voters = set(self._voted_voters)
            initial_reserved_voters = set(self._reserved_voters)

            try:
                # Double-check duplicate right before appending block
                if participation_key in self._voted_voters:
                    raise DuplicateVoteError(
                        f"Duplicate vote rejected: Voter '{voter_id}' already committed in session '{session_id}'."
                    )

                vote_timestamp = datetime.now(timezone.utc).isoformat()
                block = session_chain.add_vote(
                    voter_id=voter_id,
                    encrypted_vote=ciphertext_hex,
                    timestamp=vote_timestamp,
                )

                new_vote_id = f"VOTE-{self._vote_counter + 1:05d}"

                # Update metadata, keystore, and aggregate tallies
                self._commit_vote_metadata(
                    vote_id=new_vote_id,
                    session_id=session_id,
                    voter_id=voter_id,
                    block_index=block.index,
                    bb84_key=bb84_key,
                    choice=choice,
                )

                self._vote_counter += 1
                self._voted_voters.add(participation_key)
                self._reserved_voters.discard(participation_key)
                vote_id = new_vote_id

            except Exception:
                # ── Atomic Rollback of Blockchain and All In-Memory State ────
                while len(session_chain.chain) > initial_chain_len:
                    session_chain.chain.pop()

                self._prototype_keystore = {k: list(v) for k, v in initial_keystore.items()}
                self._prototype_vote_metadata = {k: dict(v) for k, v in initial_metadata.items()}
                self._prototype_vote_blocks = dict(initial_vote_blocks)
                self._tallies = {sid: dict(counts) for sid, counts in initial_tallies.items()}
                self._total_votes = dict(initial_total_votes)
                self._vote_counter = initial_vote_counter
                self._voted_voters = set(initial_voted_voters)
                self._reserved_voters = set(initial_reserved_voters)
                self._reserved_voters.discard(participation_key)
                raise

        # ── Step 5: Vote Receipt ─────────────────────────────────────────────
        # Note on Linkability & Ballot Privacy:
        # The receipt does not expose the plaintext vote choice.
        # However, the receipt pairs voter_id with block_index, and each block on
        # the blockchain explicitly records voter_id alongside the encrypted vote
        # (Block.vote_data['voter_id']). The ledger therefore stores voter-linked ciphertext.
        # This prototype does NOT claim anonymous or unlinkable ballot privacy.
        return {
            "success": True,
            "vote_id": vote_id,
            "session_id": session_id,
            "voter_id": voter_id,
            "timestamp": vote_timestamp,
            "message": "Vote accepted and securely recorded on blockchain.",
            "block_index": block.index,
            "block_hash": block.hash,
            "num_qubits": m3_state.qubit_count,
        }

    def _commit_vote_metadata(
        self,
        vote_id: str,
        session_id: str,
        voter_id: str,
        block_index: int,
        bb84_key: List[int],
        choice: str,
    ) -> None:
        """
        Updates in-memory prototype metadata, keystore, and aggregate tallies.
        Must be called within self._lock during Step 4.
        """
        self._prototype_keystore[vote_id] = bb84_key
        self._prototype_vote_metadata[vote_id] = {
            "session_id": session_id,
            "block_index": block_index,
            "voter_id": voter_id,
        }
        self._prototype_vote_blocks[vote_id] = block_index

        if session_id not in self._tallies:
            self._tallies[session_id] = {}
        self._tallies[session_id][choice] = self._tallies[session_id].get(choice, 0) + 1
        self._total_votes[session_id] = self._total_votes.get(session_id, 0) + 1

    def verify_vote_on_blockchain(
        self,
        session_id: str,
        vote_id: str,
        expected_choice: Optional[str] = None,
        block_index: Optional[int] = None,
        require_expected_choice: bool = False,
    ) -> Dict[str, Any]:
        """
        Retrieves the encrypted vote payload from session blockchain, decrypts using
        the in-memory prototype BB84 key, and verifies against an explicit expected_choice.

        Metadata Binding & Validation:
        - Binds vote_id to recorded session_id and block_index in engine metadata.
        - Rejects vote_id if supplied session_id does not match recorded session.
        - Rejects vote_id if supplied block_index does not match recorded block.
        - Always uses recorded block_index internally so caller-supplied block_index cannot bypass validation.

        Verification Semantics:
        - If expected_choice is omitted (None): returns decryptable=True, verified=False.
        - If require_expected_choice is True and expected_choice is omitted: raises VotingError.
        - If expected_choice is provided: returns decryptable=True, verified=(decrypted_choice == expected_choice).
        """
        sid = str(session_id).strip()
        vid = str(vote_id).strip()

        with self._lock:
            if vid not in self._prototype_vote_metadata:
                raise VotingError(f"Vote ID '{vid}' not found in prototype metadata.")

            meta = self._prototype_vote_metadata[vid]
            recorded_session = meta["session_id"]
            recorded_block = meta["block_index"]

            if sid != recorded_session:
                raise VotingError(
                    f"Vote '{vid}' was recorded in session '{recorded_session}', "
                    f"but verification requested session '{sid}'."
                )

            if block_index is not None and block_index != recorded_block:
                raise VotingError(
                    f"Supplied block_index {block_index} does not match recorded block_index {recorded_block} "
                    f"for vote '{vid}'."
                )

            session_chain = self.get_session_chain(sid)
            target_index = recorded_block

            if target_index <= 0 or target_index >= len(session_chain.chain):
                raise VotingError(f"Recorded block index {target_index} is invalid for session '{sid}'.")

            block = session_chain.chain[target_index]
            encrypted_hex = block.vote_data.get("encrypted_vote")
            if not encrypted_hex:
                raise VotingError(f"Block {target_index} contains no encrypted_vote payload.")

            if vid not in self._prototype_keystore:
                raise VotingError(f"BB84 key for vote '{vid}' not found in prototype keystore.")
            bb84_key = self._prototype_keystore[vid]

        try:
            ciphertext_bytes = bytes.fromhex(encrypted_hex)
        except ValueError as ex:
            raise VotingError(f"Ciphertext in block {target_index} is not valid hex: {ex}")

        decrypted_bits = decrypt_vote(ciphertext_bytes, bb84_key)
        from voting.ballot_encoding import decode_choice
        session_choices = self.get_session_choices(sid)
        try:
            decrypted_choice = decode_choice(session_choices, decrypted_bits)
        except Exception as e:
            raise VotingError(f"Failed to decode ballot bits: {e}")

        if require_expected_choice and expected_choice is None:
            raise VotingError("expected_choice is required when require_expected_choice=True.")

        decryptable = True
        if expected_choice is not None:
            expected_choice_str = str(expected_choice).strip()
            verified = (decrypted_choice == expected_choice_str)
        else:
            expected_choice_str = None
            verified = False

        return {
            "decryptable": decryptable,
            "verified": verified,
            "vote_id": vid,
            "session_id": sid,
            "block_index": block.index,
            "block_hash": block.hash,
            "decrypted_choice": decrypted_choice,
            "expected_choice": expected_choice_str,
            # Backward-compatibility keys
            "decrypted_candidate": decrypted_choice,
            "original_candidate": expected_choice_str,
            "matches_original": verified,
        }

    def has_voter_voted(self, session_id: str, voter_id: str) -> bool:
        """Checks if a voter has already cast a vote in the specified session."""
        sid = str(session_id).strip()
        vid = str(voter_id).strip()
        if self.use_postgres:
            return self.repo.has_voter_voted(sid, vid)
        with self._lock:
            engine_voted = (sid, vid) in self._voted_voters
            chain_voted = False
            if sid in self.session_chains:
                chain_voted = self.session_chains[sid].has_voter_voted(vid)
            return engine_voted or chain_voted

    def count_accepted_votes(
        self,
        session_id: Optional[str] = None,
        candidate_id: Optional[str] = None,
        option_id: Optional[str] = None,
        choice_id: Optional[str] = None,
    ) -> int:
        """Returns aggregate vote count from in-memory tallies without traversing vote records."""
        if self.use_postgres:
            raise NotImplementedError("Tally methods are currently unsupported in PostgreSQL mode because votes are encrypted.")
        target_choice = choice_id or candidate_id or option_id
        with self._lock:
            if session_id is not None:
                sid = str(session_id).strip()
                if target_choice is not None:
                    return self._tallies.get(sid, {}).get(str(target_choice).strip(), 0)
                return self._total_votes.get(sid, 0)
            else:
                if target_choice is not None:
                    tc = str(target_choice).strip()
                    return sum(sess_tally.get(tc, 0) for sess_tally in self._tallies.values())
                return sum(self._total_votes.values())

    def get_vote_count(
        self,
        session_id: Optional[str] = None,
        candidate_id: Optional[str] = None,
        option_id: Optional[str] = None,
        choice_id: Optional[str] = None,
    ) -> int:
        """Alias for count_accepted_votes."""
        return self.count_accepted_votes(
            session_id=session_id,
            candidate_id=candidate_id,
            option_id=option_id,
            choice_id=choice_id,
        )

    def get_tally(self, session_id: str) -> Dict[str, int]:
        """
        Returns vote counts for each candidate or option assigned to the specified session.
        Maintained as aggregate counts only. Initializes unvoted assigned choices to 0.
        """
        if self.use_postgres:
            raise NotImplementedError("Tally methods are currently unsupported in PostgreSQL mode because votes are encrypted.")
        sid = str(session_id).strip()
        with self._lock:
            if sid not in self.sessions:
                raise UnknownSessionError(f"Session '{sid}' not found.")
            return dict(self._tallies.get(sid, {}))

    def set_session_status(self, session_id: str, status: str, adjust_window: bool = True) -> None:
        """
        Updates session status in memory (useful for testing multi-session voting
        without mutating underlying CSV files).
        If adjust_window is True and setting to ACTIVE, automatically sets start_time
        and end_time so the current reference time falls inside the window.
        """
        sid = str(session_id).strip()
        with self._lock:
            if sid not in self.sessions:
                raise UnknownSessionError(f"Session '{sid}' not found.")
            norm_status = str(status).strip().upper()
            if norm_status not in self.VALID_STATUSES:
                raise VotingError(f"Invalid status '{norm_status}'.")
            self.sessions[sid]["status"] = norm_status

            if adjust_window and norm_status == "ACTIVE":
                now = self.get_now()
                self.sessions[sid]["start_time"] = (now - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
                self.sessions[sid]["end_time"] = (now + timedelta(hours=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
            elif adjust_window and norm_status == "COMPLETED":
                now = self.get_now()
                self.sessions[sid]["start_time"] = (now - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
                self.sessions[sid]["end_time"] = (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
            elif adjust_window and norm_status == "UPCOMING":
                now = self.get_now()
                self.sessions[sid]["start_time"] = (now + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
                self.sessions[sid]["end_time"] = (now + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def reset_votes(self) -> None:
        """Clears in-memory votes, participation history, and resets session blockchains."""
        with self._lock:
            self._reserved_voters.clear()
            self._voted_voters.clear()
            self._prototype_keystore.clear()
            self._prototype_vote_metadata.clear()
            self._prototype_vote_blocks.clear()
            self._vote_counter = 0
            self.session_chains = {sid: Blockchain() for sid in self.sessions}
            self._init_tallies()


# Default singleton instance for module-level helpers
_default_engine: Optional[VotingEngine] = None


def get_default_engine(data_dir: Optional[Path | str] = None) -> VotingEngine:
    """Returns or initializes the default VotingEngine instance."""
    global _default_engine
    if _default_engine is None or data_dir is not None:
        _default_engine = VotingEngine(data_dir=data_dir)
    return _default_engine


def cast_vote(
    session_id: str,
    voter_id: str,
    choice_id: Optional[str] = None,
    candidate_id: Optional[str] = None,
    option_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Module-level helper to cast a vote using the default VotingEngine instance."""
    return get_default_engine().cast_vote(
        session_id=session_id,
        voter_id=voter_id,
        choice_id=choice_id,
        candidate_id=candidate_id,
        option_id=option_id,
        idempotency_key=idempotency_key,
    )


def count_accepted_votes(
    session_id: Optional[str] = None,
    candidate_id: Optional[str] = None,
    option_id: Optional[str] = None,
    choice_id: Optional[str] = None,
) -> int:
    """Module-level helper to count accepted votes using the default VotingEngine instance."""
    return get_default_engine().count_accepted_votes(
        session_id=session_id,
        candidate_id=candidate_id,
        option_id=option_id,
        choice_id=choice_id,
    )


def get_tally(session_id: str) -> Dict[str, int]:
    """Module-level helper to retrieve tallies using the default VotingEngine instance."""
    return get_default_engine().get_tally(session_id)


def get_session_choices(session_id: str) -> List[str]:
    """Module-level helper to retrieve assigned choice IDs using the default VotingEngine instance."""
    return get_default_engine().get_session_choices(session_id)
