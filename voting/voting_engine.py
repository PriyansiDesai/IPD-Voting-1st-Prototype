"""
voting_engine.py
M2: Multi-Voter Voting Engine
Provides in-memory voting engine loading and validating M1 data.
Enforces session status, voter eligibility, candidate assignment,
and single-vote-per-session rules while supporting multi-session voting.
"""

import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


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
    In-memory voting engine for multi-voter, multi-session voting.
    Loads and validates M1 CSV data, performs eligibility and assignment checks,
    tracks voter participation to prevent duplicates, and retains votes in memory.
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

        # In-memory vote state (NO permanent plaintext CSV storage)
        self._accepted_votes: List[Dict[str, Any]] = []
        self._voted_voters: Set[Tuple[str, str]] = set()  # (session_id, voter_id)

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

    def cast_vote(self, session_id: str, voter_id: str, candidate_id: str) -> Dict[str, Any]:
        """
        Validates eligibility and records an accepted vote in memory.

        Enforces:
        - Unknown session rejection
        - Non-ACTIVE session rejection
        - Unknown voter rejection
        - Ineligible voter rejection for the session
        - Unknown candidate rejection
        - Unassigned candidate rejection for the session
        - Duplicate vote rejection within the same session

        Returns confirmation dict upon success.
        """
        session_id = str(session_id).strip()
        voter_id = str(voter_id).strip()
        candidate_id = str(candidate_id).strip()

        # 1. Validate session existence
        if session_id not in self.sessions:
            raise UnknownSessionError(f"Session '{session_id}' not found.")

        # 2. Validate session is ACTIVE
        session = self.sessions[session_id]
        status = session.get("status", "").upper()
        if status != "ACTIVE":
            raise InactiveSessionError(
                f"Session '{session_id}' is not ACTIVE (current status: '{session.get('status')}')."
            )

        # 3. Validate voter existence
        if voter_id not in self.voters:
            raise UnknownVoterError(f"Voter '{voter_id}' not found.")

        # 4. Validate voter eligibility for this session
        eligible_voters = self.session_voters.get(session_id, set())
        if voter_id not in eligible_voters:
            raise IneligibleVoterError(
                f"Voter '{voter_id}' is not eligible for session '{session_id}'."
            )

        # 5. Validate candidate existence
        if candidate_id not in self.candidates:
            raise UnknownCandidateError(f"Candidate '{candidate_id}' not found.")

        # 6. Validate candidate assigned to this session
        assigned_candidates = self.session_candidates.get(session_id, set())
        if candidate_id not in assigned_candidates:
            raise CandidateNotAssignedError(
                f"Candidate '{candidate_id}' is not assigned to session '{session_id}'."
            )

        # 7. Prevent duplicate voting within the same session
        participation_key = (session_id, voter_id)
        if participation_key in self._voted_voters:
            raise DuplicateVoteError(
                f"Duplicate vote rejected: Voter '{voter_id}' has already voted in session '{session_id}'."
            )

        # Mark voter as participated in this session
        self._voted_voters.add(participation_key)

        # Record accepted vote in memory
        vote_id = f"VOTE-{len(self._accepted_votes) + 1:05d}"
        timestamp = datetime.now(timezone.utc).isoformat()
        vote_record = {
            "vote_id": vote_id,
            "session_id": session_id,
            "candidate_id": candidate_id,
            "timestamp": timestamp,
        }
        self._accepted_votes.append(vote_record)

        return {
            "success": True,
            "vote_id": vote_id,
            "session_id": session_id,
            "voter_id": voter_id,
            "candidate_id": candidate_id,
            "timestamp": timestamp,
            "message": "Vote accepted successfully.",
        }

    def has_voter_voted(self, session_id: str, voter_id: str) -> bool:
        """Checks if a voter has already cast a vote in the specified session."""
        return (str(session_id).strip(), str(voter_id).strip()) in self._voted_voters

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
        """Clears in-memory votes and voter participation history."""
        self._accepted_votes.clear()
        self._voted_voters.clear()


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
