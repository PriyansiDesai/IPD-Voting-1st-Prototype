# benchmark_voting.py
"""
Benchmark: High-Volume Voting with M4 256-Bit BB84 Simulation.

Executes a full end-to-end voting pipeline simulation across 300–400 votes:
1. M1 Quantum Vote Encoding (superposition & measurement)
2. M4 BB84 Quantum Key Distribution with finite-key bounds (Tomamichel et al. 2012)
   extracting >= 256 bits via Toeplitz universal hashing
3. M3 Symmetric Ballot Encryption via HKDF-SHA256 and Fernet
4. M3 Blockchain Transaction Append & Cryptographic Linking

Usage:
    python benchmark_voting.py [--votes 350] [--session-id SESS-004]
"""

import argparse
import sys
import time
from typing import Any
from voting.voting_engine import VotingEngine


def run_benchmark(num_votes: int = 350, session_id: str = "SESS-004") -> dict[str, Any]:
    """
    Executes the voting benchmark casting num_votes.

    Args:
        num_votes: Number of votes to cast (recommended 300–400).
        session_id: Session ID to cast votes into (default SESS-004).

    Returns:
        dict containing execution metrics and verification status.
    """
    print("=" * 70)
    print(f"  IPD VOTING PIPELINE BENCHMARK: {num_votes} VOTES")
    print(f"  Session: {session_id} | Flow: M1 -> M4 BB84 (256-bit) -> M3 Encryption -> Blockchain")
    print("=" * 70)

    # Initialize VotingEngine with fresh session state
    engine = VotingEngine()
    engine.set_session_status(session_id, "ACTIVE")

    # Retrieve valid candidates for the session
    candidates = list(engine.session_candidates.get(session_id, []))
    if not candidates:
        raise ValueError(f"No candidates found for session '{session_id}'")

    print(f"Session '{session_id}' status: ACTIVE")
    print(f"Available candidates: {candidates}")
    print(f"Registered voter pool size: {len(engine.voters)}")

    if num_votes > len(engine.voters):
        raise ValueError(
            f"Requested {num_votes} votes exceeds available registered voters ({len(engine.voters)})"
        )

    chain = engine.get_session_chain(session_id)
    initial_block_count = len(chain.chain)
    initial_vote_count = chain.get_vote_count()

    print(f"Initial blockchain blocks: {initial_block_count} (votes: {initial_vote_count})")
    print(f"\nCasting {num_votes} votes through full BB84 + encryption pipeline...")

    start_time = time.time()
    accepted_votes = 0
    errors: list[str] = []

    progress_step = max(1, num_votes // 10)

    for i in range(1, num_votes + 1):
        voter_id = f"V{i:03d}"
        chosen_candidate = candidates[(i - 1) % len(candidates)]

        t_vote_start = time.time()
        try:
            res = engine.cast_vote(
                session_id=session_id,
                voter_id=voter_id,
                candidate_id=chosen_candidate,
            )
            t_vote_elapsed = time.time() - t_vote_start

            if res.get("success", False):
                accepted_votes += 1
                vote_id = res["vote_id"]
                # Verify that the generated BB84 key meets the 256-bit requirement
                key_len = len(engine._prototype_keystore.get(vote_id, []))
                if key_len < 256:
                    errors.append(f"Vote {voter_id}: undersized key ({key_len} bits)")
            else:
                errors.append(f"Vote {voter_id}: cast_vote returned success=False")
        except Exception as e:
            errors.append(f"Vote {voter_id}: Exception {type(e).__name__}: {e}")

        if i % progress_step == 0 or i == num_votes:
            elapsed = time.time() - start_time
            rate = i / elapsed if elapsed > 0 else 0
            print(f"  [{i:>3}/{num_votes}] votes cast ({rate:.2f} votes/sec, elapsed: {elapsed:.1f}s)")

    total_time = time.time() - start_time
    avg_latency = (total_time / num_votes) if num_votes > 0 else 0
    throughput = (num_votes / total_time) if total_time > 0 else 0

    # Verification: Blockchain integrity and tally consistency
    final_block_count = len(chain.chain)
    final_vote_count = chain.get_vote_count()
    chain_valid = chain.is_chain_valid()
    tallies = engine.get_tally(session_id)
    total_tallied = sum(tallies.values())

    all_accepted = (accepted_votes == num_votes) and (len(errors) == 0)

    print("\n" + "=" * 70)
    print("  BENCHMARK EXECUTION SUMMARY")
    print("=" * 70)
    print(f"  Command:               python benchmark_voting.py --votes {num_votes}")
    print(f"  Total Votes Attempted: {num_votes}")
    print(f"  Total Votes Accepted:  {accepted_votes} / {num_votes} ({(accepted_votes / num_votes) * 100:.1f}%)")
    print(f"  Total Runtime:         {total_time:.2f} seconds ({total_time / 60:.2f} minutes)")
    print(f"  Average Latency:       {avg_latency * 1000:.1f} ms/vote ({avg_latency:.3f} s/vote)")
    print(f"  Throughput:            {throughput:.2f} votes/second")
    print(f"  Blockchain Growth:     {initial_block_count} -> {final_block_count} blocks (+{num_votes})")
    print(f"  Blockchain Integrity:  {'VALID' if chain_valid else 'INVALID'}")
    print(f"  Total Tallied Votes:   {total_tallied} (Expected: {initial_vote_count + num_votes})")
    print(f"  Candidate Tallies:     {tallies}")
    print(f"  Status:                {'ALL VOTES ACCEPTED AND COMMITTED' if all_accepted else 'FAILURES DETECTED'}")
    print("=" * 70)

    if errors:
        print("\nErrors encountered:")
        for err in errors[:10]:
            print(f"  - {err}")
        if len(errors) > 10:
            print(f"  ... and {len(errors) - 10} more errors")

    return {
        "num_votes": num_votes,
        "accepted_votes": accepted_votes,
        "total_time": total_time,
        "avg_latency": avg_latency,
        "throughput": throughput,
        "all_accepted": all_accepted,
        "chain_valid": chain_valid,
        "errors": errors,
    }


def main():
    parser = argparse.ArgumentParser(description="High-volume BB84 voting benchmark")
    parser.add_argument("--votes", type=int, default=350, help="Number of votes to cast (300-400)")
    parser.add_argument("--session-id", type=str, default="SESS-004", help="Target session ID")
    args = parser.parse_args()

    results = run_benchmark(num_votes=args.votes, session_id=args.session_id)
    if not results["all_accepted"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
