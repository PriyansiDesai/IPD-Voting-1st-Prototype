"""
generate_synthetic_data.py
M1: Data & Voting-Environment Foundation
Generates synthetic data for voters, candidates, voting sessions, and session assignments.
Includes validation checks and summary output.
"""

import csv
from datetime import datetime
from pathlib import Path
import random

# Fixed random seed for reproducibility
SEED = 42
random.seed(SEED)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"

VALID_STATUSES = {"UPCOMING", "ACTIVE", "COMPLETED", "CLOSED", "ARCHIVED"}


def generate_voters(count: int = 50) -> list[dict]:
    first_names = [
        "Aarav", "Diya", "Rohan", "Priya", "Kabir", "Ananya", "Vihaan", "Ishita",
        "Arjun", "Meera", "Aditya", "Sanya", "Dev", "Kavya", "Rahul", "Tanvi",
        "Nikhil", "Pooja", "Siddharth", "Neha", "Varun", "Rhea", "Manish", "Tara",
        "Karan", "Shreya", "Gaurav", "Simran", "Akash", "Anushka", "Vikram", "Sneha"
    ]
    last_names = [
        "Sharma", "Patel", "Verma", "Singh", "Mehta", "Rao", "Gupta", "Sen",
        "Reddy", "Joshi", "Malhotra", "Kapoor", "Chopra", "Nair", "Bhat", "Iyer",
        "Saxena", "Das", "Deshmukh", "Menon", "Bose", "Ghosh", "Agarwal", "Pillai"
    ]
    departments = [
        "Engineering", "Product", "Operations", "Finance",
        "Human Resources", "Legal", "Research", "Marketing"
    ]
    roles = [
        "Software Engineer", "Senior Software Engineer", "Product Manager",
        "Engineering Lead", "Operations Analyst", "Financial Analyst",
        "HR Specialist", "Legal Counsel", "Research Scientist", "Staff Engineer"
    ]

    voters = []
    for i in range(1, count + 1):
        voter_id = f"V{i:03d}"
        first = random.choice(first_names)
        last = random.choice(last_names)
        name = f"{first} {last}"
        dept = random.choice(departments)
        role = random.choice(roles)
        voters.append({
            "voter_id": voter_id,
            "name": name,
            "department": dept,
            "role": role,
        })
    return voters


def generate_candidates() -> list[dict]:
    candidate_list = [
        ("C001", "Aarav Sharma"),
        ("C002", "Diya Patel"),
        ("C003", "Rohan Verma"),
        ("C004", "Priya Singh"),
        ("C005", "Kabir Mehta"),
        ("C006", "Ananya Rao"),
        ("C007", "Vihaan Gupta"),
        ("C008", "Ishita Sen"),
        ("C009", "Arjun Reddy"),
        ("C010", "Meera Joshi"),
        ("C011", "Vikram Malhotra"),
        ("C012", "Neha Kapoor"),
    ]
    return [{"candidate_id": cid, "candidate_name": cname} for cid, cname in candidate_list]


def generate_voting_sessions() -> list[dict]:
    sessions = [
        {
            "session_id": "SESS-001",
            "title": "Annual Board Employee Representative",
            "description": "Election of employee representative to the Executive Board for 2026-2027.",
            "start_time": "2026-09-01T09:00:00Z",
            "end_time": "2026-09-01T17:00:00Z",
            "status": "COMPLETED",
        },
        {
            "session_id": "SESS-002",
            "title": "Engineering Architecture Steering Committee",
            "description": "Selection of committee lead for next-generation quantum-safe architecture.",
            "start_time": "2026-09-10T10:00:00Z",
            "end_time": "2026-09-10T18:00:00Z",
            "status": "COMPLETED",
        },
        {
            "session_id": "SESS-003",
            "title": "Q3 Workplace Policy & Hybrid Standards",
            "description": "Org-wide vote on upcoming hybrid and remote work framework revisions.",
            "start_time": "2026-09-20T08:30:00Z",
            "end_time": "2026-09-22T20:00:00Z",
            "status": "ACTIVE",
        },
        {
            "session_id": "SESS-004",
            "title": "Annual Innovation & Research Grant Allocation",
            "description": "Selection of research initiative recipient for annual innovation grant funding.",
            "start_time": "2026-10-05T09:00:00Z",
            "end_time": "2026-10-06T17:00:00Z",
            "status": "UPCOMING",
        },
        {
            "session_id": "SESS-005",
            "title": "Health & Benefits Provider Evaluation",
            "description": "Employee committee review and vote on prospective corporate benefits partners.",
            "start_time": "2026-10-15T09:00:00Z",
            "end_time": "2026-10-16T18:00:00Z",
            "status": "UPCOMING",
        },
    ]
    return sessions


def assign_session_candidates(sessions: list[dict], candidates: list[dict]) -> list[dict]:
    candidate_ids = [c["candidate_id"] for c in candidates]
    assignments = []

    for s in sessions:
        # Choose 3-5 candidates for each session
        num_options = random.randint(3, 5)
        chosen = random.sample(candidate_ids, num_options)
        for cid in chosen:
            assignments.append({
                "session_id": s["session_id"],
                "candidate_id": cid,
            })
    return assignments


def assign_session_voters(sessions: list[dict], voters: list[dict]) -> list[dict]:
    voter_ids = [v["voter_id"] for v in voters]
    assignments = []

    for s in sessions:
        # Assign different eligible voters (e.g. between 25 and 45 voters per session)
        num_eligible = random.randint(25, 45)
        chosen_voters = sorted(random.sample(voter_ids, num_eligible))
        for vid in chosen_voters:
            assignments.append({
                "session_id": s["session_id"],
                "voter_id": vid,
            })
    return assignments


def save_csv(file_path: Path, data: list[dict], fieldnames: list[str]) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(data)


def validate_m1_dataset(
    voters: list[dict],
    candidates: list[dict],
    sessions: list[dict],
    session_voters: list[dict],
    session_candidates: list[dict],
) -> dict:
    """
    Concise validation suite checking IDs, relationships, duplicates,
    timestamps, status values, option limits, and ensuring no plaintext vote data.
    """
    errors = []

    # 1. Unique IDs
    voter_ids = [v["voter_id"] for v in voters]
    if len(voter_ids) != len(set(voter_ids)):
        errors.append("Duplicate voter_id found in voters.")

    cand_ids = [c["candidate_id"] for c in candidates]
    if len(cand_ids) != len(set(cand_ids)):
        errors.append("Duplicate candidate_id found in candidates.")

    sess_ids = [s["session_id"] for s in sessions]
    if len(sess_ids) != len(set(sess_ids)):
        errors.append("Duplicate session_id found in sessions.")

    # 2. Foreign Key Relationships
    valid_voter_ids = set(voter_ids)
    valid_cand_ids = set(cand_ids)
    valid_sess_ids = set(sess_ids)

    for sv in session_voters:
        if sv["session_id"] not in valid_sess_ids:
            errors.append(f"Invalid session_id '{sv['session_id']}' in session_voters.")
        if sv["voter_id"] not in valid_voter_ids:
            errors.append(f"Invalid voter_id '{sv['voter_id']}' in session_voters.")

    for sc in session_candidates:
        if sc["session_id"] not in valid_sess_ids:
            errors.append(f"Invalid session_id '{sc['session_id']}' in session_candidates.")
        if sc["candidate_id"] not in valid_cand_ids:
            errors.append(f"Invalid candidate_id '{sc['candidate_id']}' in session_candidates.")

    # 3. Duplicate checks in junction tables
    sv_pairs = [(sv["session_id"], sv["voter_id"]) for sv in session_voters]
    if len(sv_pairs) != len(set(sv_pairs)):
        errors.append("Duplicate (session_id, voter_id) found in session_voters.")

    sc_pairs = [(sc["session_id"], sc["candidate_id"]) for sc in session_candidates]
    if len(sc_pairs) != len(set(sc_pairs)):
        errors.append("Duplicate (session_id, candidate_id) found in session_candidates.")

    # 4. Timestamps & Status values
    for s in sessions:
        if s["status"] not in VALID_STATUSES:
            errors.append(f"Invalid status '{s['status']}' in session {s['session_id']}.")
        try:
            start_dt = datetime.fromisoformat(s["start_time"].replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(s["end_time"].replace("Z", "+00:00"))
            if start_dt >= end_dt:
                errors.append(f"start_time is not before end_time in session {s['session_id']}.")
        except ValueError as ex:
            errors.append(f"Timestamp parsing error in session {s['session_id']}: {ex}")

    # 5. Options count per session (3-5 options)
    options_per_session = {}
    for sc in session_candidates:
        sid = sc["session_id"]
        options_per_session[sid] = options_per_session.get(sid, 0) + 1

    for sid in sess_ids:
        count = options_per_session.get(sid, 0)
        if not (3 <= count <= 5):
            errors.append(f"Session {sid} has {count} options; expected between 3 and 5.")

    # 6. Absence of plaintext vote dataset
    # Verify no file or records link (voter_id, session_id, candidate_id) together
    combined_vote_records = [
        rec for rec in session_voters if "candidate_id" in rec
    ]
    if combined_vote_records:
        errors.append("CRITICAL: Found plaintext vote linkage (voter_id + session_id + candidate_id).")

    return {
        "passed": len(errors) == 0,
        "errors": errors,
        "summary": {
            "voters_count": len(voters),
            "candidates_count": len(candidates),
            "sessions_count": len(sessions),
            "session_voters_count": len(session_voters),
            "session_candidates_count": len(session_candidates),
        }
    }


def main():
    random.seed(SEED)
    print("=" * 60)
    print("M1: GENERATING SYNTHETIC VOTING ENVIRONMENT DATA")
    print("=" * 60)

    voters = generate_voters(50)
    candidates = generate_candidates()
    sessions = generate_voting_sessions()
    session_candidates = assign_session_candidates(sessions, candidates)
    session_voters = assign_session_voters(sessions, voters)

    # Validate before saving
    val_result = validate_m1_dataset(
        voters, candidates, sessions, session_voters, session_candidates
    )

    if not val_result["passed"]:
        print("Validation FAILED prior to saving:")
        for err in val_result["errors"]:
            print(f" - {err}")
        raise ValueError("Data validation failed.")

    # Write files
    save_csv(DATA_DIR / "voters.csv", voters, ["voter_id", "name", "department", "role"])
    save_csv(DATA_DIR / "candidates.csv", candidates, ["candidate_id", "candidate_name"])
    save_csv(DATA_DIR / "voting_sessions.csv", sessions, [
        "session_id", "title", "description", "start_time", "end_time", "status"
    ])
    save_csv(DATA_DIR / "session_voters.csv", session_voters, ["session_id", "voter_id"])
    save_csv(DATA_DIR / "session_candidates.csv", session_candidates, ["session_id", "candidate_id"])

    print("\nFiles written successfully to data/:")
    print(f" - voters.csv             : {len(voters)} records")
    print(f" - candidates.csv         : {len(candidates)} records")
    print(f" - voting_sessions.csv    : {len(sessions)} records")
    print(f" - session_voters.csv     : {len(session_voters)} records")
    print(f" - session_candidates.csv : {len(session_candidates)} records")

    print("\nValidation Summary:")
    print(" - Unique IDs verified    : [OK]")
    print(" - Relationships verified : [OK]")
    print(" - No duplicate pairs     : [OK]")
    print(" - Timestamps & statuses  : [OK]")
    print(" - 3-5 options/session    : [OK]")
    print(" - No plaintext ballots   : [OK]")
    print("=" * 60)
    print("M1 GENERATION & VALIDATION COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    main()
