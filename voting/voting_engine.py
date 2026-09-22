"""
voting_engine.py
M3: Multi-Voter Voting Engine with Quantum & Blockchain Pipeline Integration.
Enforces M1 data validation, encodes candidate choices into quantum circuits (vol1),
performs BB84 quantum key distribution (vol2), encrypts votes via post-quantum
symmetric derivation (vol3), and commits votes onto per-session tamper-evident
blockchains (vol4).
"""

import csv
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Set, Tuple

# Ensure project root is in sys.path so volume modules can be imported
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from vol1_encoding import encode_vote
from vol2_bb84 import get_secure_key
from vol3_pqc import encrypt_vote, decrypt_vote
from vol4_blockchain import Blockchain, Block


class VotingError(ValueError):
    """Base exception for voting engine errors (subclasses ValueError)."""
    pass


class UnknownSessionError(VotingError):
    """Raised when session_id does not exist."""
    pass


class InactiveSessionError(VotingError):
    """Raised when session is not in ACTIVE status."""
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


class DuplicateVoteError(VotingError):
    """Raised when a voter attempts to vote more than once in the same session."""
    pass


class VotingEngine:
    """
    Multi-voter, multi-session voting engine with quantum-safe blockchain integration.
    - Loads and validates M1 CSV reference data.
    - Enforces business eligibility and duplicate prevention.
    - Encodes vote options into quantum qubit circuits (vol1).
    - Negotiates 8-bit quantum keys via BB84 simulation (vol2).
    - Derives 256-bit AES keys and encrypts ballots (vol3).
    - Appends encrypted ballots to an independent, tamper-evident Blockchain per session (vol4).
    - Maintains an in-memory prototype keystore strictly for M3 testing/verification.
    """

    VALID_STATUSES = {"UPCOMING", "ACTIVE", "COMPLETED", "CLOSED", "ARCHIVED"}

    def __init__(self, data_dir: Optional[Path | str] = None, auto_load: bool = True):
        if data_dir is None:
            self.data_dir = Path(__file__).resolve().parent.parent / "data"
        else:
            self.data_dir = Path(data_dir)

        # Loaded reference datasets from M1 CSVs
        self.voters: Dict[str, Dict[str, str]] = {}
        self.candidates: Dict[str, Dict[str, str]] = {}
        self.sessions: Dict[str, Dict[str, str]] = {}
        self.session_voters: Dict[str, Set[str]] = {}
        self.session_candidates: Dict[str, Set[str]] = {}

        # M3: Blockchain storage per session (maintains session isolation)
        self.session_chains: Dict[str, Blockchain] = {}

        # In-memory vote state (NO permanent plaintext CSV storage)
        self._accepted_votes: List[Dict[str, Any]] = []
        self._voted_voters: Set[Tuple[str, str]] = set()  # (session_id, voter_id)

        # Prototype-only in-memory key storage for M3 testing and verification.
        # Maps vote_id -> bb84_key (list[int]).
        # NOTE: This is strictly for prototype demonstration and test verification.
        # Production systems will employ decentralized or zero-knowledge key escrow.
        self._prototype_keystore: Dict[str, List[int]] = {}

        if auto_load:
            self.load_data()
            self.validate_data()

    def load_data(self, data_dir: Optional[Path | str] = None) -> None:
        """Loads voters, candidates, sessions, and junction mappings from M1 CSVs."""
        target_dir = Path(data_dir) if data_dir is not None else self.data_dir

        voters_file = target_dir / "voters.csv"
        candidates_file = target_dir / "candidates.csv"
        sessions_file = target_dir / "voting_sessions.csv"
        session_voters_file = target_dir / "session_voters.csv"
        session_candidates_file = target_dir / "session_candidates.csv"

        for file_path in [voters_file, candidates_file, sessions_file, session_voters_file, session_candidates_file]:
            if not file_path.is_file():
                raise FileNotFoundError(f"Required M1 data file not found: {file_path}")

        # 1. Voters
        self.voters = {}
        with open(voters_file, mode="r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                vid = row["voter_id"].strip()
                self.voters[vid] = {k: v.strip() for k, v in row.items()}

        # 2. Candidates
        self.candidates = {}
        with open(candidates_file, mode="r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                cid = row["candidate_id"].strip()
                self.candidates[cid] = {k: v.strip() for k, v in row.items()}

        # 3. Sessions
        self.sessions = {}
        with open(sessions_file, mode="r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                sid = row["session_id"].strip()
                self.sessions[sid] = {k: v.strip() for k, v in row.items()}

        # 4. Session Voters (Junction)
        self.session_voters = {sid: set() for sid in self.sessions}
        with open(session_voters_file, mode="r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                sid = row["session_id"].strip()
                vid = row["voter_id"].strip()
                if sid not in self.session_voters:
                    self.session_voters[sid] = set()
                self.session_voters[sid].add(vid)

        # 5. Session Candidates (Junction)
        self.session_candidates = {sid: set() for sid in self.sessions}
        with open(session_candidates_file, mode="r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                sid = row["session_id"].strip()
                cid = row["candidate_id"].strip()
                if sid not in self.session_candidates:
                    self.session_candidates[sid] = set()
                self.session_candidates[sid].add(cid)

        # 6. Initialize dedicated blockchain instance per session
        self.session_chains = {sid: Blockchain() for sid in self.sessions}

    def validate_data(self) -> None:
        """Performs consistency and integrity checks on loaded M1 data."""
        errors = []

        # Validate sessions status
        for sid, sess in self.sessions.items():
            status = sess.get("status")
            if status not in self.VALID_STATUSES:
                errors.append(f"Session {sid} has invalid status '{status}'.")

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

        if errors:
            raise VotingError("Data validation failed:\n" + "\n".join(errors))

    def get_session_chain(self, session_id: str) -> Blockchain:
        """Returns the Blockchain instance for the given session_id, creating one if needed."""
        sid = str(session_id).strip()
        if sid not in self.session_chains:
            self.session_chains[sid] = Blockchain()
        return self.session_chains[sid]

    def cast_vote(self, session_id: str, voter_id: str, candidate_id: str) -> Dict[str, Any]:
        """
        Executes the complete M3 Quantum & Blockchain voting pipeline:
        1. M1/M2 Validation:
           - Unknown session rejection
           - Non-ACTIVE session rejection
           - Unknown voter rejection
           - Ineligible voter rejection for the session
           - Unknown candidate rejection
           - Unassigned candidate rejection for the session
           - Duplicate vote rejection within the same session
        2. Quantum Encoding (vol1):
           - Obtains deterministic sorted candidate IDs assigned to the session.
           - Encodes choice into quantum qubits and measures.
        3. BB84 Key Exchange (vol2):
           - Generates a fresh 8-bit quantum key per accepted vote.
        4. Post-Quantum Encryption (vol3):
           - Derives 256-bit AES key and encrypts candidate_id.
           - Converts ciphertext bytes to hex string for ledger storage.
        5. Blockchain Ledger Storage (vol4):
           - Appends block to the dedicated session Blockchain.
        6. Prototype Key & State Management:
           - Saves key in in-memory prototype keystore for verification.
           - Records voter participation.
           - Returns comprehensive vote receipt with blockchain block details.
        """
        session_id = str(session_id).strip()
        voter_id = str(voter_id).strip()
        candidate_id = str(candidate_id).strip()

        # ── Step 1: M2 Validation ─────────────────────────────────────────────
        # Validate session existence
        if session_id not in self.sessions:
            raise UnknownSessionError(f"Session '{session_id}' not found.")

        # Validate session is ACTIVE
        session = self.sessions[session_id]
        status = session.get("status", "").upper()
        if status != "ACTIVE":
            raise InactiveSessionError(
                f"Session '{session_id}' is not ACTIVE (current status: '{session.get('status')}')."
            )

        # Validate voter existence
        if voter_id not in self.voters:
            raise UnknownVoterError(f"Voter '{voter_id}' not found.")

        # Validate voter eligibility for this session
        eligible_voters = self.session_voters.get(session_id, set())
        if voter_id not in eligible_voters:
            raise IneligibleVoterError(
                f"Voter '{voter_id}' is not eligible for session '{session_id}'."
            )

        # Validate candidate existence
        if candidate_id not in self.candidates:
            raise UnknownCandidateError(f"Candidate '{candidate_id}' not found.")

        # Validate candidate assigned to this session
        assigned_candidates = self.session_candidates.get(session_id, set())
        if candidate_id not in assigned_candidates:
            raise CandidateNotAssignedError(
                f"Candidate '{candidate_id}' is not assigned to session '{session_id}'."
            )

        # Prevent duplicate voting within the same session (engine-level check)
        participation_key = (session_id, voter_id)
        if participation_key in self._voted_voters:
            raise DuplicateVoteError(
                f"Duplicate vote rejected: Voter '{voter_id}' has already voted in session '{session_id}'."
            )

        # Prevent duplicate voting at the blockchain layer for this session
        session_chain = self.get_session_chain(session_id)
        if session_chain.has_voter_voted(voter_id):
            raise DuplicateVoteError(
                f"Duplicate vote rejected on blockchain: Voter '{voter_id}' already has a committed block in session '{session_id}'."
            )

        # ── Step 2: Quantum Vote Encoding (vol1) ───────────────────────────────
        # Deterministically sort candidate IDs for consistent qubit state indexing
        candidates_list = sorted(list(assigned_candidates))
        encoded_result = encode_vote(candidates=candidates_list, chosen_candidate=candidate_id)
        encoded_candidate = encoded_result["decoded_candidate"]

        # ── Step 3: BB84 Quantum Key Distribution (vol2) ───────────────────────
        # Generate fresh BB84 key for every accepted vote
        bb84_key = get_secure_key(min_length=8)

        # ── Step 4: Quantum-Safe Encryption (vol3) ─────────────────────────────
        # Encrypt the decoded candidate string and convert to hex for blockchain
        ciphertext_bytes = encrypt_vote(vote_data=encoded_candidate, bb84_key=bb84_key)
        ciphertext_hex = ciphertext_bytes.hex()

        # ── Step 5: Blockchain Ledger Storage (vol4) ───────────────────────────
        vote_timestamp = datetime.now(timezone.utc).isoformat()
        block = session_chain.add_vote(
            voter_id=voter_id,
            encrypted_vote=ciphertext_hex,
            timestamp=vote_timestamp,
        )

        # ── Step 6: Prototype Key Handling & Memory State ─────────────────────
        vote_id = f"VOTE-{len(self._accepted_votes) + 1:05d}"
        self._prototype_keystore[vote_id] = bb84_key

        # Mark voter as participated in this session
        self._voted_voters.add(participation_key)

        # Record accepted vote in memory (linking vote_id with block and session)
        vote_record = {
            "vote_id": vote_id,
            "session_id": session_id,
            "candidate_id": candidate_id,
            "block_index": block.index,
            "block_hash": block.hash,
            "timestamp": vote_timestamp,
        }
        self._accepted_votes.append(vote_record)

        # ── Step 7: Useful Vote Receipt ───────────────────────────────────────
        return {
            "success": True,
            "vote_id": vote_id,
            "session_id": session_id,
            "voter_id": voter_id,
            "candidate_id": candidate_id,
            "timestamp": vote_timestamp,
            "message": "Vote accepted and securely recorded on blockchain.",
            "block_index": block.index,
            "block_hash": block.hash,
            "prev_hash": block.prev_hash,
            "num_qubits": encoded_result.get("num_qubits"),
        }

    def verify_vote_on_blockchain(
        self,
        session_id: str,
        vote_id: str,
        block_index: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        M3 Verification/Testing Method:
        - Retrieves the encrypted vote payload from the session blockchain.
        - Converts the hex string back to raw ciphertext bytes.
        - Retrieves the corresponding in-memory BB84 key from the prototype keystore.
        - Decrypts the ciphertext using vol3_pqc.decrypt_vote().
        - Verifies that the decrypted candidate matches the original candidate.
        """
        sid = str(session_id).strip()
        vid = str(vote_id).strip()

        # Locate vote record in memory
        matching_votes = [
            v for v in self._accepted_votes
            if v["vote_id"] == vid and v["session_id"] == sid
        ]
        if not matching_votes:
            raise VotingError(f"Vote '{vid}' not found in session '{sid}'.")

        vote_record = matching_votes[0]
        target_index = block_index if block_index is not None else vote_record["block_index"]
        expected_candidate = vote_record["candidate_id"]

        # Retrieve encrypted vote from the session blockchain
        session_chain = self.get_session_chain(sid)
        if target_index <= 0 or target_index >= len(session_chain.chain):
            raise VotingError(f"Block index {target_index} is invalid for session '{sid}'.")

        block = session_chain.chain[target_index]
        encrypted_hex = block.vote_data.get("encrypted_vote")
        if not encrypted_hex:
            raise VotingError(f"Block {target_index} contains no encrypted_vote payload.")

        # Convert hex back to bytes
        try:
            ciphertext_bytes = bytes.fromhex(encrypted_hex)
        except ValueError as ex:
            raise VotingError(f"Ciphertext in block {target_index} is not valid hex: {ex}")

        # Retrieve prototype in-memory BB84 key
        if vid not in self._prototype_keystore:
            raise VotingError(f"BB84 key for vote '{vid}' not found in prototype keystore.")
        bb84_key = self._prototype_keystore[vid]

        # Decrypt using vol3_pqc.decrypt_vote
        decrypted_candidate = decrypt_vote(ciphertext_bytes, bb84_key)
        matches = (decrypted_candidate == expected_candidate)

        return {
            "verified": matches,
            "vote_id": vid,
            "session_id": sid,
            "block_index": block.index,
            "block_hash": block.hash,
            "decrypted_candidate": decrypted_candidate,
            "original_candidate": expected_candidate,
            "matches_original": matches,
        }

    def has_voter_voted(self, session_id: str, voter_id: str) -> bool:
        """Checks if a voter has already cast a vote in the specified session."""
        sid = str(session_id).strip()
        vid = str(voter_id).strip()
        engine_voted = (sid, vid) in self._voted_voters
        chain_voted = False
        if sid in self.session_chains:
            chain_voted = self.session_chains[sid].has_voter_voted(vid)
        return engine_voted or chain_voted

    def count_accepted_votes(
        self,
        session_id: Optional[str] = None,
        candidate_id: Optional[str] = None,
    ) -> int:
        """
        Returns the total number of accepted votes stored in memory.
        Can optionally filter by session_id and/or candidate_id.
        """
        votes = self._accepted_votes
        if session_id is not None:
            sid = str(session_id).strip()
            votes = [v for v in votes if v["session_id"] == sid]
        if candidate_id is not None:
            cid = str(candidate_id).strip()
            votes = [v for v in votes if v["candidate_id"] == cid]
        return len(votes)

    def get_vote_count(
        self,
        session_id: Optional[str] = None,
        candidate_id: Optional[str] = None,
    ) -> int:
        """Alias for count_accepted_votes."""
        return self.count_accepted_votes(session_id=session_id, candidate_id=candidate_id)

    def get_tally(self, session_id: str) -> Dict[str, int]:
        """
        Returns vote counts for each candidate assigned to the specified session.
        Initializes unvoted assigned candidates to 0.
        """
        sid = str(session_id).strip()
        if sid not in self.sessions:
            raise UnknownSessionError(f"Session '{sid}' not found.")

        assigned = self.session_candidates.get(sid, set())
        tally: Dict[str, int] = {cid: 0 for cid in assigned}
        for v in self._accepted_votes:
            if v["session_id"] == sid and v["candidate_id"] in tally:
                tally[v["candidate_id"]] += 1
        return tally

    def set_session_status(self, session_id: str, status: str) -> None:
        """
        Updates session status in memory (useful for testing multi-session voting
        without mutating underlying CSV files).
        """
        sid = str(session_id).strip()
        if sid not in self.sessions:
            raise UnknownSessionError(f"Session '{sid}' not found.")
        norm_status = str(status).strip().upper()
        if norm_status not in self.VALID_STATUSES:
            raise VotingError(f"Invalid status '{norm_status}'.")
        self.sessions[sid]["status"] = norm_status

    def reset_votes(self) -> None:
        """Clears in-memory votes, voter participation history, and resets session blockchains."""
        self._accepted_votes.clear()
        self._voted_voters.clear()
        self._prototype_keystore.clear()
        self.session_chains = {sid: Blockchain() for sid in self.sessions}


# Default singleton instance for module-level helpers
_default_engine: Optional[VotingEngine] = None


def get_default_engine(data_dir: Optional[Path | str] = None) -> VotingEngine:
    """Returns or initializes the default VotingEngine instance."""
    global _default_engine
    if _default_engine is None or data_dir is not None:
        _default_engine = VotingEngine(data_dir=data_dir)
    return _default_engine


def cast_vote(session_id: str, voter_id: str, candidate_id: str) -> Dict[str, Any]:
    """Module-level helper to cast a vote using the default VotingEngine instance."""
    return get_default_engine().cast_vote(session_id, voter_id, candidate_id)


def count_accepted_votes(
    session_id: Optional[str] = None,
    candidate_id: Optional[str] = None,
) -> int:
    """Module-level helper to count accepted votes using the default VotingEngine instance."""
    return get_default_engine().count_accepted_votes(session_id, candidate_id)
