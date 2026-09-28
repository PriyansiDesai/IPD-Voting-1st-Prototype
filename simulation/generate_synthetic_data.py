"""
M1: Synthetic voting environment generator.

Creates voters, people running in candidate elections, decision options,
voting sessions, and eligibility/choice assignments.

This generator does not create or store cast votes.

Dataset targets
---------------
- 400 synthetic voters
- 12+ people candidates (15 provided)
- 15+ ballot options (18 provided; all assigned to >= 1 session)
- Sample sessions for all three session_type values
- At least one active session with 400 eligible voters (300-400 vote demo)
- Realistic smaller eligibility subsets on other sessions
- Dynamic timezone-aware reference time (UTC by default; configurable for demos)
- Status vs. time-window consistency validation
- Reproducible with SEED = 42
"""

import argparse
import csv
import os
import random
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

SEED = 42
VOTER_COUNT = 400

# Optional demo reference time (ISO-8601 UTC string, e.g. "2026-09-28T09:00:00Z").
# When None, datetime.now(timezone.utc) is used by default.
DEMO_REFERENCE_TIME: str | None = None

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"

VALID_STATUSES = {"UPCOMING", "ACTIVE", "COMPLETED", "CLOSED", "ARCHIVED"}
VALID_SESSION_TYPES = {"candidate_election", "yes_no", "single_choice"}
VALID_OPTION_TYPES = {"yes_no", "abstain", "project", "provider", "policy"}


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------

def _parse_utc(ts: str) -> datetime:
    """Parse an ISO-8601 UTC timestamp string into an aware datetime."""
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _format_utc(dt: datetime) -> str:
    """Format an aware UTC datetime as an ISO-8601 string ending with Z."""
    dt_utc = dt.astimezone(timezone.utc)
    return dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def resolve_reference_now(cli_arg: str | None = None) -> tuple[datetime, str]:
    """
    Determine the timezone-aware reference 'now' timestamp.
    Returns (reference_datetime, source_description).
    Precedence:
      1. Explicit CLI argument (--reference-time)
      2. Environment variable (DEMO_REFERENCE_TIME)
      3. Module constant (DEMO_REFERENCE_TIME)
      4. Default: current UTC time
    """
    if cli_arg:
        ref = _parse_utc(cli_arg)
        return ref, f"explicit CLI argument (--reference-time {cli_arg})"

    env_val = os.environ.get("DEMO_REFERENCE_TIME")
    if env_val:
        ref = _parse_utc(env_val)
        return ref, f"environment variable DEMO_REFERENCE_TIME ({env_val})"

    if DEMO_REFERENCE_TIME:
        ref = _parse_utc(DEMO_REFERENCE_TIME)
        return ref, f"module constant DEMO_REFERENCE_TIME ({DEMO_REFERENCE_TIME})"

    now = datetime.now(timezone.utc).replace(microsecond=0)
    return now, f"current UTC time ({_format_utc(now)})"


# ---------------------------------------------------------------------------
# Data generators
# ---------------------------------------------------------------------------

def generate_voters(count: int = VOTER_COUNT) -> list[dict]:
    """Generate `count` synthetic voters with unique V-prefixed IDs."""
    first_names = [
        "Aarav", "Diya", "Rohan", "Priya", "Kabir", "Ananya", "Vihaan",
        "Ishita", "Arjun", "Meera", "Aditya", "Sanya", "Dev", "Kavya",
        "Rahul", "Tanvi", "Nikhil", "Pooja", "Siddharth", "Neha",
    ]
    last_names = [
        "Sharma", "Patel", "Verma", "Singh", "Mehta", "Rao", "Gupta",
        "Sen", "Reddy", "Joshi", "Malhotra", "Kapoor", "Chopra", "Nair",
    ]
    departments = [
        "Engineering", "Product", "Operations", "Finance",
        "Human Resources", "Legal", "Research", "Marketing",
    ]
    roles = [
        "Software Engineer", "Product Manager", "Engineering Lead",
        "Operations Analyst", "Financial Analyst", "Research Scientist",
    ]

    voters = []
    for i in range(1, count + 1):
        voters.append({
            "voter_id": f"V{i:03d}",
            "name": f"{random.choice(first_names)} {random.choice(last_names)}",
            "department": random.choice(departments),
            "role": random.choice(roles),
        })

    return voters


def generate_candidates() -> list[dict]:
    """People who are candidates in candidate-election sessions (>= 12, here 15)."""
    names = [
        "Aarav Sharma",
        "Diya Patel",
        "Rohan Verma",
        "Priya Singh",
        "Kabir Mehta",
        "Ananya Rao",
        "Vihaan Gupta",
        "Ishita Sen",
        "Arjun Reddy",
        "Meera Joshi",
        "Vikram Malhotra",
        "Neha Kapoor",
        "Ravi Chopra",
        "Sunita Nair",
        "Aryan Mishra",
    ]

    return [
        {"candidate_id": f"C{i:03d}", "candidate_name": name}
        for i, name in enumerate(names, start=1)
    ]


def generate_ballot_options() -> list[dict]:
    """
    Decision choices and alternatives; these are NOT people.
    18 options (>= 15 required).

    option_type taxonomy
    --------------------
    yes_no   -- the Yes and No options required by yes_no sessions
    abstain  -- optional Abstain choice for yes/no sessions; option_type is 'abstain'
    project  -- single-choice project options
    provider -- single-choice provider options
    policy   -- single-choice policy options
    """
    return [
        {"option_id": "O001", "option_label": "Yes",              "option_type": "yes_no"},
        {"option_id": "O002", "option_label": "No",               "option_type": "yes_no"},
        {"option_id": "O003", "option_label": "Abstain",          "option_type": "abstain"},
        {"option_id": "O004", "option_label": "Project Alpha",    "option_type": "project"},
        {"option_id": "O005", "option_label": "Project Beta",     "option_type": "project"},
        {"option_id": "O006", "option_label": "Project Gamma",    "option_type": "project"},
        {"option_id": "O007", "option_label": "Project Delta",    "option_type": "project"},
        {"option_id": "O008", "option_label": "Project Epsilon",  "option_type": "project"},
        {"option_id": "O009", "option_label": "Project Zeta",     "option_type": "project"},
        {"option_id": "O010", "option_label": "Provider North",   "option_type": "provider"},
        {"option_id": "O011", "option_label": "Provider Central", "option_type": "provider"},
        {"option_id": "O012", "option_label": "Provider South",   "option_type": "provider"},
        {"option_id": "O013", "option_label": "Provider East",    "option_type": "provider"},
        {"option_id": "O014", "option_label": "Provider West",    "option_type": "provider"},
        {"option_id": "O015", "option_label": "Provider Global",  "option_type": "provider"},
        {"option_id": "O016", "option_label": "Policy Option A",  "option_type": "policy"},
        {"option_id": "O017", "option_label": "Policy Option B",  "option_type": "policy"},
        {"option_id": "O018", "option_label": "Policy Option C",  "option_type": "policy"},
    ]


def generate_voting_sessions(reference_now: datetime) -> list[dict]:
    """
    Sample sessions covering all three session types.
    Time windows are dynamically generated relative to the given reference_now.

    Status correctness relative to reference_now:
    ---------------------------------------------------
    SESS-001  candidate_election  ended 27d ago   -> COMPLETED
    SESS-002  yes_no              ended 17d ago   -> COMPLETED
    SESS-003  yes_no              ended 6d ago    -> COMPLETED
    SESS-004  candidate_election  now in progress -> ACTIVE    (400-voter demo)
    SESS-005  single_choice       starts in 7d    -> UPCOMING
    SESS-006  single_choice       starts in 14d   -> UPCOMING
    SESS-007  yes_no              starts in 21d   -> UPCOMING
    SESS-008  single_choice       starts in 28d   -> UPCOMING  (policy session)
    """
    return [
        {
            "session_id": "SESS-001",
            "title": "Board Representative Election",
            "question": "Who should represent employees on the board?",
            "session_type": "candidate_election",
            "start_time": _format_utc(reference_now - timedelta(days=28)),
            "end_time": _format_utc(reference_now - timedelta(days=27, hours=16)),
            "status": "COMPLETED",
        },
        {
            "session_id": "SESS-002",
            "title": "Project Alpha Approval",
            "question": "Should the organisation approve Project Alpha?",
            "session_type": "yes_no",
            "start_time": _format_utc(reference_now - timedelta(days=18)),
            "end_time": _format_utc(reference_now - timedelta(days=17, hours=16)),
            "status": "COMPLETED",
        },
        {
            "session_id": "SESS-003",
            "title": "Hybrid Work Policy",
            "question": "Should the proposed hybrid work policy be adopted?",
            "session_type": "yes_no",
            "start_time": _format_utc(reference_now - timedelta(days=8)),
            "end_time": _format_utc(reference_now - timedelta(days=6)),
            "status": "COMPLETED",
        },
        {
            "session_id": "SESS-004",
            "title": "Department Head Election",
            "question": "Who should be elected as Department Head?",
            "session_type": "candidate_election",
            "start_time": _format_utc(reference_now - timedelta(hours=2)),
            "end_time": _format_utc(reference_now + timedelta(hours=10)),
            "status": "ACTIVE",
        },
        {
            "session_id": "SESS-005",
            "title": "Research Grant Funding",
            "question": "Which project should receive the research grant?",
            "session_type": "single_choice",
            "start_time": _format_utc(reference_now + timedelta(days=7)),
            "end_time": _format_utc(reference_now + timedelta(days=8, hours=8)),
            "status": "UPCOMING",
        },
        {
            "session_id": "SESS-006",
            "title": "Benefits Provider Selection",
            "question": "Which provider should the organisation select?",
            "session_type": "single_choice",
            "start_time": _format_utc(reference_now + timedelta(days=14)),
            "end_time": _format_utc(reference_now + timedelta(days=15, hours=9)),
            "status": "UPCOMING",
        },
        {
            "session_id": "SESS-007",
            "title": "Remote Work Allowance Policy",
            "question": "Should the remote work allowance be increased?",
            "session_type": "yes_no",
            "start_time": _format_utc(reference_now + timedelta(days=21)),
            "end_time": _format_utc(reference_now + timedelta(days=22, hours=8)),
            "status": "UPCOMING",
        },
        {
            "session_id": "SESS-008",
            "title": "Corporate Sustainability Initiative Priority",
            "question": "Which sustainability policy initiative should be funded for Q4?",
            "session_type": "single_choice",
            "start_time": _format_utc(reference_now + timedelta(days=28)),
            "end_time": _format_utc(reference_now + timedelta(days=29, hours=8)),
            "status": "UPCOMING",
        },
    ]


# ---------------------------------------------------------------------------
# Assignment functions
# ---------------------------------------------------------------------------

def assign_session_candidates(sessions: list[dict]) -> list[dict]:
    """
    Assign people candidates only to candidate_election sessions.

    SESS-001: all 15 candidates (large completed election)
    SESS-004: all 15 candidates (active 300-400 vote demo session)
    """
    all_candidate_ids = [f"C{i:03d}" for i in range(1, 16)]
    candidates_by_session = {
        "SESS-001": all_candidate_ids,
        "SESS-004": all_candidate_ids,
    }

    assignments = []
    for session in sessions:
        for candidate_id in candidates_by_session.get(session["session_id"], []):
            assignments.append({
                "session_id": session["session_id"],
                "candidate_id": candidate_id,
            })

    return assignments


def assign_session_options(sessions: list[dict]) -> list[dict]:
    """
    Assign decision options only to yes_no and single_choice sessions.

    - yes_no sessions require Yes (O001) and No (O002) of option_type 'yes_no'.
      Abstain (O003, option_type 'abstain') is permitted as an optional choice.
      SESS-002 and SESS-007 include Abstain; SESS-003 is pure Yes/No, showing optionality.
    - single_choice sessions receive non-yes_no options (>= 2 required):
      SESS-005: project options O004-O009
      SESS-006: provider options O010-O015
      SESS-008: policy options O016-O018
    Every option in ballot_options.csv (O001-O018) is assigned to >= 1 session.
    """
    options_by_session = {
        "SESS-002": ["O001", "O002", "O003"],
        "SESS-003": ["O001", "O002"],
        "SESS-007": ["O001", "O002", "O003"],
        "SESS-005": ["O004", "O005", "O006", "O007", "O008", "O009"],
        "SESS-006": ["O010", "O011", "O012", "O013", "O014", "O015"],
        "SESS-008": ["O016", "O017", "O018"],
    }

    assignments = []
    for session in sessions:
        for option_id in options_by_session.get(session["session_id"], []):
            assignments.append({
                "session_id": session["session_id"],
                "option_id": option_id,
            })

    return assignments


def assign_session_voters(
    sessions: list[dict],
    voters: list[dict],
) -> list[dict]:
    """
    Assign eligible voters per session using realistic subset sizes.

    Eligibility plan
    ----------------
    SESS-001  400  all voters - completed large election
    SESS-002  250  smaller subset
    SESS-003  200  smaller subset
    SESS-004  400  all voters - active 300-400 vote demo session
    SESS-005  150  small subset - upcoming
    SESS-006  100  small subset - upcoming
    SESS-007  300  medium subset - upcoming
    SESS-008  200  smaller policy subset - upcoming
    """
    eligibility_sizes = {
        "SESS-001": 400,
        "SESS-002": 250,
        "SESS-003": 200,
        "SESS-004": 400,
        "SESS-005": 150,
        "SESS-006": 100,
        "SESS-007": 300,
        "SESS-008": 200,
    }

    voter_ids = [voter["voter_id"] for voter in voters]

    assignments = []
    for session in sessions:
        sid = session["session_id"]
        count = min(eligibility_sizes.get(sid, len(voter_ids)), len(voter_ids))
        chosen_ids = sorted(random.sample(voter_ids, count))
        for voter_id in chosen_ids:
            assignments.append({"session_id": sid, "voter_id": voter_id})

    return assignments


# ---------------------------------------------------------------------------
# CSV persistence
# ---------------------------------------------------------------------------

def save_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_data(
    voters: list[dict],
    candidates: list[dict],
    ballot_options: list[dict],
    sessions: list[dict],
    session_voters: list[dict],
    session_candidates: list[dict],
    session_options: list[dict],
    reference_now: datetime,
) -> list[str]:
    """
    M1 data-integrity checks.

    1.  Unique IDs (voter, candidate, option, session).
    2.  Valid foreign keys in junction tables.
    3.  No duplicate (session, entity) assignments.
    4.  Nonempty eligible-voter sets for every session.
    5.  Valid session_type, status, and option_type enum values.
    6.  Valid timestamp pairs (start_time < end_time).
    7.  Status vs. time-window consistency relative to reference_now:
        - UPCOMING: starts in the future (start_time > reference_now).
        - ACTIVE: currently within its window (start_time <= reference_now < end_time).
        - COMPLETED: has ended (end_time <= reference_now).
        - CLOSED: allowed to have ended early (start_time <= reference_now).
        - ARCHIVED: historical (start_time <= reference_now).
    8.  candidate_election: >= 2 candidates, 0 options.
    9.  yes_no:
        - 0 candidates.
        - Yes and No options of option_type 'yes_no' required.
        - Abstain of option_type 'abstain' permitted.
        - No single-choice options (project, provider, policy) permitted.
    10. single_choice:
        - 0 candidates.
        - >= 2 options of non-yes_no types.
        - No option of option_type 'yes_no' permitted.
    11. Full coverage:
        - Every row in ballot_options.csv must be assigned to >= 1 session.
        - Every row in candidates.csv must be assigned to >= 1 session.
    """
    errors: list[str] = []

    # 1. Unique IDs
    voter_ids     = [r["voter_id"]     for r in voters]
    candidate_ids = [r["candidate_id"] for r in candidates]
    option_ids    = [r["option_id"]    for r in ballot_options]
    session_ids   = [r["session_id"]   for r in sessions]

    if len(voter_ids) != len(set(voter_ids)):
        errors.append("Duplicate voter_id detected.")
    if len(candidate_ids) != len(set(candidate_ids)):
        errors.append("Duplicate candidate_id detected.")
    if len(option_ids) != len(set(option_ids)):
        errors.append("Duplicate option_id detected.")
    if len(session_ids) != len(set(session_ids)):
        errors.append("Duplicate session_id detected.")

    valid_voters     = set(voter_ids)
    valid_candidates = set(candidate_ids)
    valid_options    = set(option_ids)
    valid_sessions   = set(session_ids)

    # 2. Foreign-key checks
    for row in session_voters:
        if row["session_id"] not in valid_sessions:
            errors.append(f"session_voters: unknown session_id '{row['session_id']}'.")
        if row["voter_id"] not in valid_voters:
            errors.append(f"session_voters: unknown voter_id '{row['voter_id']}'.")

    for row in session_candidates:
        if row["session_id"] not in valid_sessions:
            errors.append(f"session_candidates: unknown session_id '{row['session_id']}'.")
        if row["candidate_id"] not in valid_candidates:
            errors.append(f"session_candidates: unknown candidate_id '{row['candidate_id']}'.")

    for row in session_options:
        if row["session_id"] not in valid_sessions:
            errors.append(f"session_options: unknown session_id '{row['session_id']}'.")
        if row["option_id"] not in valid_options:
            errors.append(f"session_options: unknown option_id '{row['option_id']}'.")

    # 3. No duplicate assignments
    voter_links     = [(r["session_id"], r["voter_id"])     for r in session_voters]
    candidate_links = [(r["session_id"], r["candidate_id"]) for r in session_candidates]
    option_links    = [(r["session_id"], r["option_id"])    for r in session_options]

    if len(voter_links) != len(set(voter_links)):
        errors.append("Duplicate (session_id, voter_id) in session_voters.")
    if len(candidate_links) != len(set(candidate_links)):
        errors.append("Duplicate (session_id, candidate_id) in session_candidates.")
    if len(option_links) != len(set(option_links)):
        errors.append("Duplicate (session_id, option_id) in session_options.")

    # 4 & 5. Build lookup maps and validate enums
    voters_by_session     = {}
    for r in session_voters:
        voters_by_session.setdefault(r["session_id"], set()).add(r["voter_id"])

    candidates_by_session = {}
    for r in session_candidates:
        candidates_by_session.setdefault(r["session_id"], set()).add(r["candidate_id"])

    options_by_session    = {}
    for r in session_options:
        options_by_session.setdefault(r["session_id"], set()).add(r["option_id"])

    option_by_id = {r["option_id"]: r for r in ballot_options}

    for opt in ballot_options:
        otype = opt.get("option_type")
        if otype not in VALID_OPTION_TYPES:
            errors.append(f"Option {opt.get('option_id')}: invalid option_type '{otype}'.")

    for session in sessions:
        sid          = session["session_id"]
        session_type = session.get("session_type", "")
        status       = session.get("status", "")

        # 4. Nonempty voter eligibility
        if not voters_by_session.get(sid):
            errors.append(f"{sid}: no eligible voters assigned.")

        # 5. Valid enums
        if session_type not in VALID_SESSION_TYPES:
            errors.append(f"{sid}: invalid session_type '{session_type}'.")
        if status not in VALID_STATUSES:
            errors.append(f"{sid}: invalid status '{status}'.")

        # 6. Timestamp validity
        try:
            start = _parse_utc(session["start_time"])
            end   = _parse_utc(session["end_time"])
            if start >= end:
                errors.append(f"{sid}: start_time must be before end_time.")
        except (ValueError, KeyError):
            errors.append(f"{sid}: invalid or missing timestamp.")
            continue

        # 7. Status vs. time-window consistency relative to reference_now
        if status == "UPCOMING":
            if start <= reference_now:
                errors.append(
                    f"{sid}: status is UPCOMING but start_time ({session['start_time']}) "
                    f"is not in the future relative to reference time ({_format_utc(reference_now)})."
                )
        elif status == "ACTIVE":
            if reference_now < start:
                errors.append(
                    f"{sid}: status is ACTIVE but session has not started yet "
                    f"(start_time={session['start_time']} > reference_time={_format_utc(reference_now)})."
                )
            if reference_now >= end:
                errors.append(
                    f"{sid}: status is ACTIVE but session has already ended "
                    f"(end_time={session['end_time']} <= reference_time={_format_utc(reference_now)})."
                )
        elif status == "COMPLETED":
            if end > reference_now:
                errors.append(
                    f"{sid}: status is COMPLETED but end_time ({session['end_time']}) "
                    f"is in the future relative to reference time ({_format_utc(reference_now)})."
                )
        elif status == "CLOSED":
            # Allowed to have ended early; must have started in the past
            if start > reference_now:
                errors.append(
                    f"{sid}: status is CLOSED but start_time ({session['start_time']}) is in the future."
                )
        elif status == "ARCHIVED":
            # Historical session; must have started in the past
            if start > reference_now:
                errors.append(
                    f"{sid}: status is ARCHIVED but start_time ({session['start_time']}) is in the future."
                )

        assigned_candidates = candidates_by_session.get(sid, set())
        assigned_options    = options_by_session.get(sid, set())

        # 8. candidate_election rules
        if session_type == "candidate_election":
            if len(assigned_candidates) < 2:
                errors.append(
                    f"{sid}: candidate_election needs >= 2 people candidates "
                    f"(found {len(assigned_candidates)})."
                )
            if assigned_options:
                errors.append(f"{sid}: candidate_election must not have ballot options.")

        # 9. yes_no rules
        elif session_type == "yes_no":
            if assigned_candidates:
                errors.append(f"{sid}: yes_no session must not have people candidates.")
            # Verify option types: only 'yes_no' and 'abstain' allowed
            for oid in assigned_options:
                opt = option_by_id.get(oid)
                if opt and opt.get("option_type") not in {"yes_no", "abstain"}:
                    errors.append(
                        f"{sid}: yes_no session contains option '{oid}' with disallowed "
                        f"option_type '{opt.get('option_type')}'; only 'yes_no' and 'abstain' are permitted."
                    )
            # Require Yes and No options with option_type 'yes_no'
            yes_no_labels = {
                option_by_id[oid]["option_label"].strip().lower()
                for oid in assigned_options
                if oid in option_by_id and option_by_id[oid].get("option_type") == "yes_no"
            }
            if "yes" not in yes_no_labels:
                errors.append(
                    f"{sid}: yes_no session must include a Yes option of type yes_no."
                )
            if "no" not in yes_no_labels:
                errors.append(
                    f"{sid}: yes_no session must include a No option of type yes_no."
                )

        # 10. single_choice rules
        elif session_type == "single_choice":
            if assigned_candidates:
                errors.append(f"{sid}: single_choice session must not have people candidates.")
            # No yes_no options allowed in single_choice
            for oid in assigned_options:
                opt = option_by_id.get(oid)
                if opt and opt.get("option_type") == "yes_no":
                    errors.append(
                        f"{sid}: single_choice session cannot contain option '{oid}' of type 'yes_no'."
                    )
            non_yes_no = {
                oid for oid in assigned_options
                if oid in option_by_id
                and option_by_id[oid].get("option_type") != "yes_no"
            }
            if len(non_yes_no) < 2:
                errors.append(
                    f"{sid}: single_choice needs >= 2 non-yes_no options "
                    f"(found {len(non_yes_no)})."
                )

    # 11. Full coverage checks: ensure all ballot options and candidates are assigned
    all_assigned_option_ids = set()
    for s_opts in options_by_session.values():
        all_assigned_option_ids.update(s_opts)
    unassigned_options = valid_options - all_assigned_option_ids
    if unassigned_options:
        errors.append(
            f"Unassigned ballot options detected: {sorted(unassigned_options)}. "
            "Every row in ballot_options.csv must be assigned to at least one session."
        )

    all_assigned_candidate_ids = set()
    for s_cands in candidates_by_session.values():
        all_assigned_candidate_ids.update(s_cands)
    unassigned_candidates = valid_candidates - all_assigned_candidate_ids
    if unassigned_candidates:
        errors.append(
            f"Unassigned candidates detected: {sorted(unassigned_candidates)}. "
            "Every row in candidates.csv must be assigned to at least one session."
        )

    return errors


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(description="M1 Synthetic voting environment generator")
    parser.add_argument(
        "--reference-time",
        "-r",
        type=str,
        default=None,
        help="Explicit reference time in ISO-8601 UTC format (e.g. '2026-09-28T09:00:00Z') for repeatable demonstrations. Defaults to current UTC time.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    reference_now, ref_source = resolve_reference_now(args.reference_time)

    random.seed(SEED)

    voters          = generate_voters()
    candidates      = generate_candidates()
    ballot_options  = generate_ballot_options()
    sessions        = generate_voting_sessions(reference_now)

    session_voters     = assign_session_voters(sessions, voters)
    session_candidates = assign_session_candidates(sessions)
    session_options    = assign_session_options(sessions)

    errors = validate_data(
        voters=voters,
        candidates=candidates,
        ballot_options=ballot_options,
        sessions=sessions,
        session_voters=session_voters,
        session_candidates=session_candidates,
        session_options=session_options,
        reference_now=reference_now,
    )

    # Print summary
    print("=" * 64)
    print("M1 SYNTHETIC DATA GENERATOR -- RECORD COUNTS")
    print("=" * 64)
    print(f"  Reference time used         : {_format_utc(reference_now)}")
    print(f"  Reference source            : {ref_source}")
    print(f"  Voters                      : {len(voters)}")
    print(f"  People candidates           : {len(candidates)}")
    print(f"  Ballot options              : {len(ballot_options)}")
    print(f"  Voting sessions             : {len(sessions)}")
    print(f"  Eligibility assignments     : {len(session_voters)}")
    print(f"  Session-candidate links     : {len(session_candidates)}")
    print(f"  Session-option links        : {len(session_options)}")

    voter_counts = Counter(r["session_id"] for r in session_voters)
    options_by_sess = {}
    for r in session_options:
        options_by_sess.setdefault(r["session_id"], []).append(r["option_id"])
    cands_by_sess = {}
    for r in session_candidates:
        cands_by_sess.setdefault(r["session_id"], []).append(r["candidate_id"])

    print()
    print("  Sessions overview:")
    for session in sessions:
        sid = session["session_id"]
        stype = session["session_type"]
        status = session["status"]
        v_count = voter_counts.get(sid, 0)
        c_count = len(cands_by_sess.get(sid, []))
        o_count = len(options_by_sess.get(sid, []))
        opts_summary = (
            f"{c_count} candidates" if stype == "candidate_election"
            else f"{o_count} options ({','.join(options_by_sess.get(sid, []))})"
        )
        print(
            f"    {sid}  ({stype:20s}  {status:10s})  "
            f"{v_count:>3d} voters  |  {opts_summary}"
        )

    print()
    print("=" * 64)
    print("M1 VALIDATION RESULTS")
    print("=" * 64)

    if errors:
        print(f"  FAILED -- {len(errors)} error(s) found:")
        for err in errors:
            print(f"    x  {err}")
        raise ValueError(
            "M1 validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
        )

    print("  PASSED -- all 11 checks succeeded.")
    print("=" * 64)

    save_csv(DATA_DIR / "voters.csv",     voters,     ["voter_id", "name", "department", "role"])
    save_csv(DATA_DIR / "candidates.csv", candidates, ["candidate_id", "candidate_name"])
    save_csv(DATA_DIR / "ballot_options.csv", ballot_options, ["option_id", "option_label", "option_type"])
    save_csv(DATA_DIR / "voting_sessions.csv", sessions,
             ["session_id", "title", "question", "session_type", "start_time", "end_time", "status"])
    save_csv(DATA_DIR / "session_voters.csv",     session_voters,     ["session_id", "voter_id"])
    save_csv(DATA_DIR / "session_candidates.csv", session_candidates, ["session_id", "candidate_id"])
    save_csv(DATA_DIR / "session_options.csv",    session_options,    ["session_id", "option_id"])

    print()
    print("All CSV files written to:", DATA_DIR)


if __name__ == "__main__":
    main()
