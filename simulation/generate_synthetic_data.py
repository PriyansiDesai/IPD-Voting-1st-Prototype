"""
M1: Synthetic voting environment.

Creates voters, people running in candidate elections, decision options,
voting sessions, and eligibility/choice assignments.

This generator does not create or store cast votes.
"""

import csv
import random
from datetime import datetime
from pathlib import Path

SEED = 42
VOTER_COUNT = 400
ELIGIBLE_PER_SESSION = 400

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"

VALID_STATUSES = {"UPCOMING", "ACTIVE", "COMPLETED", "CLOSED", "ARCHIVED"}
VALID_SESSION_TYPES = {"candidate_election", "yes_no", "single_choice"}


def generate_voters(count: int = VOTER_COUNT) -> list[dict]:
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
    """People who are candidates in candidate-election sessions."""
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
    ]

    return [
        {"candidate_id": f"C{i:03d}", "candidate_name": name}
        for i, name in enumerate(names, start=1)
    ]


def generate_ballot_options() -> list[dict]:
    """Decision choices and alternatives; these are not people."""
    return [
        {"option_id": "O001", "option_label": "Yes", "option_type": "yes_no"},
        {"option_id": "O002", "option_label": "No", "option_type": "yes_no"},
        {"option_id": "O003", "option_label": "Abstain", "option_type": "yes_no"},

        {"option_id": "O004", "option_label": "Project Alpha", "option_type": "project"},
        {"option_id": "O005", "option_label": "Project Beta", "option_type": "project"},
        {"option_id": "O006", "option_label": "Project Gamma", "option_type": "project"},
        {"option_id": "O007", "option_label": "Project Delta", "option_type": "project"},
        {"option_id": "O008", "option_label": "Project Epsilon", "option_type": "project"},
        {"option_id": "O009", "option_label": "Project Zeta", "option_type": "project"},

        {"option_id": "O010", "option_label": "Provider North", "option_type": "provider"},
        {"option_id": "O011", "option_label": "Provider Central", "option_type": "provider"},
        {"option_id": "O012", "option_label": "Provider South", "option_type": "provider"},
        {"option_id": "O013", "option_label": "Provider East", "option_type": "provider"},
        {"option_id": "O014", "option_label": "Provider West", "option_type": "provider"},
        {"option_id": "O015", "option_label": "Provider Global", "option_type": "provider"},
    ]


def generate_voting_sessions() -> list[dict]:
    return [
        {
            "session_id": "SESS-001",
            "title": "Board Representative Election",
            "question": "Who should represent employees on the board?",
            "session_type": "candidate_election",
            "start_time": "2026-09-01T09:00:00Z",
            "end_time": "2026-09-01T17:00:00Z",
            "status": "COMPLETED",
        },
        {
            "session_id": "SESS-002",
            "title": "Project Alpha Approval",
            "question": "Should the organisation approve Project Alpha?",
            "session_type": "yes_no",
            "start_time": "2026-09-10T10:00:00Z",
            "end_time": "2026-09-10T18:00:00Z",
            "status": "COMPLETED",
        },
        {
            "session_id": "SESS-003",
            "title": "Hybrid Work Policy",
            "question": "Should the proposed hybrid work policy be adopted?",
            "session_type": "yes_no",
            "start_time": "2026-09-20T08:30:00Z",
            "end_time": "2026-09-22T20:00:00Z",
            "status": "ACTIVE",
        },
        {
            "session_id": "SESS-004",
            "title": "Research Grant Funding",
            "question": "Which project should receive the research grant?",
            "session_type": "single_choice",
            "start_time": "2026-10-05T09:00:00Z",
            "end_time": "2026-10-06T17:00:00Z",
            "status": "UPCOMING",
        },
        {
            "session_id": "SESS-005",
            "title": "Benefits Provider Selection",
            "question": "Which provider should the organisation select?",
            "session_type": "single_choice",
            "start_time": "2026-10-15T09:00:00Z",
            "end_time": "2026-10-16T18:00:00Z",
            "status": "UPCOMING",
        },
    ]


def assign_session_candidates(sessions: list[dict]) -> list[dict]:
    """Assign people only to candidate-election sessions."""
    all_candidate_ids = [f"C{i:03d}" for i in range(1, 13)]
    candidates_by_session = {"SESS-001": all_candidate_ids}

    assignments = []
    for session in sessions:
        candidate_ids = candidates_by_session.get(session["session_id"], [])
        for candidate_id in candidate_ids:
            assignments.append({
                "session_id": session["session_id"],
                "candidate_id": candidate_id,
            })

    return assignments


def assign_session_options(sessions: list[dict]) -> list[dict]:
    """Assign decision options only to yes/no and single-choice sessions."""
    options_by_session = {
        "SESS-002": ["O001", "O002", "O003"],
        "SESS-003": ["O001", "O002", "O003"],
        "SESS-004": ["O004", "O005", "O006", "O007", "O008", "O009"],
        "SESS-005": ["O010", "O011", "O012", "O013", "O014", "O015"],
    }

    assignments = []
    for session in sessions:
        option_ids = options_by_session.get(session["session_id"], [])
        for option_id in option_ids:
            assignments.append({
                "session_id": session["session_id"],
                "option_id": option_id,
            })

    return assignments


def assign_session_voters(
    sessions: list[dict],
    voters: list[dict],
    eligible_per_session: int = ELIGIBLE_PER_SESSION,
) -> list[dict]:
    """Assign eligible voters. This does not record whether they cast a vote."""
    voter_ids = [voter["voter_id"] for voter in voters]
    count = min(eligible_per_session, len(voter_ids))

    assignments = []
    for session in sessions:
        chosen_ids = sorted(random.sample(voter_ids, count))
        for voter_id in chosen_ids:
            assignments.append({
                "session_id": session["session_id"],
                "voter_id": voter_id,
            })

    return assignments


def save_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def validate_data(
    voters: list[dict],
    candidates: list[dict],
    ballot_options: list[dict],
    sessions: list[dict],
    session_voters: list[dict],
    session_candidates: list[dict],
    session_options: list[dict],
) -> list[str]:
    errors = []

    voter_ids = [row["voter_id"] for row in voters]
    candidate_ids = [row["candidate_id"] for row in candidates]
    option_ids = [row["option_id"] for row in ballot_options]
    session_ids = [row["session_id"] for row in sessions]

    if len(voter_ids) != len(set(voter_ids)):
        errors.append("Duplicate voter_id.")
    if len(candidate_ids) != len(set(candidate_ids)):
        errors.append("Duplicate candidate_id.")
    if len(option_ids) != len(set(option_ids)):
        errors.append("Duplicate option_id.")
    if len(session_ids) != len(set(session_ids)):
        errors.append("Duplicate session_id.")

    valid_voters = set(voter_ids)
    valid_candidates = set(candidate_ids)
    valid_options = set(option_ids)
    valid_sessions = set(session_ids)

    for row in session_voters:
        if row["session_id"] not in valid_sessions:
            errors.append(f"Unknown session in session_voters: {row['session_id']}")
        if row["voter_id"] not in valid_voters:
            errors.append(f"Unknown voter in session_voters: {row['voter_id']}")

    for row in session_candidates:
        if row["session_id"] not in valid_sessions:
            errors.append(f"Unknown session in session_candidates: {row['session_id']}")
        if row["candidate_id"] not in valid_candidates:
            errors.append(f"Unknown candidate: {row['candidate_id']}")

    for row in session_options:
        if row["session_id"] not in valid_sessions:
            errors.append(f"Unknown session in session_options: {row['session_id']}")
        if row["option_id"] not in valid_options:
            errors.append(f"Unknown option: {row['option_id']}")

    voter_links = [(row["session_id"], row["voter_id"]) for row in session_voters]
    candidate_links = [
        (row["session_id"], row["candidate_id"]) for row in session_candidates
    ]
    option_links = [(row["session_id"], row["option_id"]) for row in session_options]

    if len(voter_links) != len(set(voter_links)):
        errors.append("Duplicate session-voter assignment.")
    if len(candidate_links) != len(set(candidate_links)):
        errors.append("Duplicate session-candidate assignment.")
    if len(option_links) != len(set(option_links)):
        errors.append("Duplicate session-option assignment.")

    candidates_by_session = {}
    for row in session_candidates:
        candidates_by_session.setdefault(row["session_id"], set()).add(
            row["candidate_id"]
        )

    options_by_session = {}
    for row in session_options:
        options_by_session.setdefault(row["session_id"], set()).add(row["option_id"])

    option_by_id = {row["option_id"]: row for row in ballot_options}

    for session in sessions:
        session_id = session["session_id"]
        session_type = session["session_type"]

        if session_type not in VALID_SESSION_TYPES:
            errors.append(f"Invalid session_type in {session_id}.")
        if session["status"] not in VALID_STATUSES:
            errors.append(f"Invalid status in {session_id}.")

        try:
            start = datetime.fromisoformat(session["start_time"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(session["end_time"].replace("Z", "+00:00"))
            if start >= end:
                errors.append(f"Invalid time range in {session_id}.")
        except ValueError:
            errors.append(f"Invalid timestamp in {session_id}.")

        assigned_candidates = candidates_by_session.get(session_id, set())
        assigned_options = options_by_session.get(session_id, set())

        if session_type == "candidate_election":
            if len(assigned_candidates) < 2:
                errors.append(f"{session_id} needs at least two people candidates.")
            if assigned_options:
                errors.append(
                    f"{session_id} is a candidate election and must not have ballot options."
                )

        elif session_type == "yes_no":
            if assigned_candidates:
                errors.append(
                    f"{session_id} is a decision session and must not have people candidates."
                )

            labels = {
                option_by_id[option_id]["option_label"].strip().lower()
                for option_id in assigned_options
                if option_id in option_by_id
            }
            if not {"yes", "no"}.issubset(labels):
                errors.append(f"{session_id} must include Yes and No options.")

        elif session_type == "single_choice":
            if assigned_candidates:
                errors.append(
                    f"{session_id} is a decision session and must not have people candidates."
                )
            if len(assigned_options) < 2:
                errors.append(f"{session_id} needs at least two ballot options.")

    return errors


def main() -> None:
    random.seed(SEED)

    voters = generate_voters()
    candidates = generate_candidates()
    ballot_options = generate_ballot_options()
    sessions = generate_voting_sessions()

    session_voters = assign_session_voters(sessions, voters)
    session_candidates = assign_session_candidates(sessions)
    session_options = assign_session_options(sessions)

    errors = validate_data(
        voters=voters,
        candidates=candidates,
        ballot_options=ballot_options,
        sessions=sessions,
        session_voters=session_voters,
        session_candidates=session_candidates,
        session_options=session_options,
    )
    if errors:
        raise ValueError("M1 validation failed: " + "; ".join(errors))

    save_csv(
        DATA_DIR / "voters.csv",
        voters,
        ["voter_id", "name", "department", "role"],
    )
    save_csv(
        DATA_DIR / "candidates.csv",
        candidates,
        ["candidate_id", "candidate_name"],
    )
    save_csv(
        DATA_DIR / "ballot_options.csv",
        ballot_options,
        ["option_id", "option_label", "option_type"],
    )
    save_csv(
        DATA_DIR / "voting_sessions.csv",
        sessions,
        [
            "session_id",
            "title",
            "question",
            "session_type",
            "start_time",
            "end_time",
            "status",
        ],
    )
    save_csv(
        DATA_DIR / "session_voters.csv",
        session_voters,
        ["session_id", "voter_id"],
    )
    save_csv(
        DATA_DIR / "session_candidates.csv",
        session_candidates,
        ["session_id", "candidate_id"],
    )
    save_csv(
        DATA_DIR / "session_options.csv",
        session_options,
        ["session_id", "option_id"],
    )

    print("M1 data generated and validated.")
    print(f"Voters: {len(voters)}")
    print(f"People candidates: {len(candidates)}")
    print(f"Ballot options: {len(ballot_options)}")
    print(f"Sessions: {len(sessions)}")
    print(f"Eligibility assignments: {len(session_voters)}")
    print(f"Session-candidate assignments: {len(session_candidates)}")
    print(f"Session-option assignments: {len(session_options)}")


if __name__ == "__main__":
    main()