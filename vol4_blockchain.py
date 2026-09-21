# vol4_blockchain.py

import hashlib
import json
from datetime import datetime, timezone


# ─────────────────────────────────────────────
#  Block
# ─────────────────────────────────────────────

class Block:
    """
    Represents a single block in the voting blockchain.

    Each block stores one vote record (voter_id, encrypted_vote, timestamp),
    links itself to the previous block via prev_hash, and carries its own
    SHA-256 hash so the chain is tamper-evident.
    """

    def __init__(
        self,
        index: int,
        vote_data: dict,
        prev_hash: str,
        timestamp: str | None = None,
    ):
        self.index      = index
        self.vote_data  = vote_data          # {"voter_id": ..., "encrypted_vote": ..., "timestamp": ...}
        self.prev_hash  = prev_hash
        self.timestamp  = timestamp or datetime.now(timezone.utc).isoformat()
        self.hash       = self._compute_hash()

    # ── hashing ──────────────────────────────

    def _compute_hash(self) -> str:
        """
        Returns the SHA-256 hash of this block's contents.
        The hash covers index, vote_data, prev_hash, and timestamp —
        so any change to any field will produce a completely different hash.
        """
        block_string = json.dumps(
            {
                "index":     self.index,
                "vote_data": self.vote_data,
                "prev_hash": self.prev_hash,
                "timestamp": self.timestamp,
            },
            sort_keys=True,   # deterministic ordering
        )
        return hashlib.sha256(block_string.encode()).hexdigest()

    def recompute_hash(self) -> str:
        """Re-computes and updates self.hash.  Call after mutating any field."""
        self.hash = self._compute_hash()
        return self.hash

    # ── display ──────────────────────────────

    def to_dict(self) -> dict:
        """Returns a plain-dict representation of this block (useful for JSON export)."""
        return {
            "index":      self.index,
            "timestamp":  self.timestamp,
            "vote_data":  self.vote_data,
            "prev_hash":  self.prev_hash,
            "hash":       self.hash,
        }

    def __repr__(self) -> str:
        return (
            f"Block(index={self.index}, "
            f"voter={self.vote_data.get('voter_id', '?')}, "
            f"hash={self.hash[:12]}...)"
        )


# ─────────────────────────────────────────────
#  Blockchain
# ─────────────────────────────────────────────

class Blockchain:
    """
    An append-only chain of Block objects for the voting system.

    The chain always starts with a hard-coded Genesis Block (index 0)
    that anchors the hash chain.  Every subsequent block's prev_hash
    must equal the preceding block's hash — any tampering breaks
    this link and is caught by is_chain_valid().
    """

    GENESIS_VOTER_ID = "GENESIS"

    def __init__(self):
        self.chain: list[Block] = []
        self._create_genesis_block()

    # ── internal ─────────────────────────────

    def _create_genesis_block(self) -> None:
        """Appends the immutable genesis block (index 0) to an empty chain."""
        genesis_vote = {
            "voter_id":      self.GENESIS_VOTER_ID,
            "encrypted_vote": "0" * 64,   # 64 zeroes — obviously not a real vote
            "timestamp":      "1970-01-01T00:00:00+00:00",
        }
        genesis = Block(
            index=0,
            vote_data=genesis_vote,
            prev_hash="0" * 64,           # no previous block
            timestamp="1970-01-01T00:00:00+00:00",
        )
        self.chain.append(genesis)

    @property
    def latest_block(self) -> Block:
        """Returns the most recently added block."""
        return self.chain[-1]

    # ── public API ───────────────────────────

    def add_vote(
        self,
        voter_id: str,
        encrypted_vote: str,
        timestamp: str | None = None,
    ) -> Block:
        """
        Creates a new block carrying the given vote and appends it to the chain.

        Parameters
        ----------
        voter_id       : Unique identifier for the voter (e.g. "V001").
        encrypted_vote : The encrypted vote payload — today this can be a dummy
                         hex string; later it will be the real PQC ciphertext.
        timestamp      : ISO-8601 timestamp; auto-generated (UTC now) if omitted.

        Returns
        -------
        The newly created and appended Block.
        """
        vote_data = {
            "voter_id":       voter_id,
            "encrypted_vote": encrypted_vote,
            "timestamp":      timestamp or datetime.now(timezone.utc).isoformat(),
        }
        new_block = Block(
            index     = len(self.chain),
            vote_data = vote_data,
            prev_hash = self.latest_block.hash,
        )
        self.chain.append(new_block)
        return new_block

    def is_chain_valid(self) -> bool:
        """
        Validates the entire chain by checking two things for every block
        (except the genesis block):

        1. The block's stored hash still matches a fresh re-computation —
           i.e. the block's own data has not been altered.
        2. The block's prev_hash matches the actual hash of the preceding block —
           i.e. no block has been removed or reordered.

        Returns True only if both conditions hold for every block.
        """
        for i in range(1, len(self.chain)):
            current  = self.chain[i]
            previous = self.chain[i - 1]

            # Rule 1 — block's own hash must still be consistent with its contents
            if current.hash != current._compute_hash():
                return False

            # Rule 2 — block must point back to its predecessor
            if current.prev_hash != previous.hash:
                return False

        return True

    def get_vote_count(self) -> int:
        """Returns the number of votes stored (genesis block excluded)."""
        return len(self.chain) - 1

    def has_voter_voted(self, voter_id: str) -> bool:
        """
        Returns True if voter_id already has a block on the chain.
        Prevents double-voting at the storage layer.
        """
        for block in self.chain[1:]:   # skip genesis
            if block.vote_data.get("voter_id") == voter_id:
                return True
        return False

    def view_chain(self, show_full_hashes: bool = False) -> None:
        """
        Prints a human-readable summary of every block in the chain.

        Parameters
        ----------
        show_full_hashes : If False (default), only the first 16 hex characters
                           of each hash are shown for readability.
        """
        trim = (lambda h: h) if show_full_hashes else (lambda h: h[:16] + "...")

        print("=" * 60)
        print(f"  BLOCKCHAIN  --  {len(self.chain)} block(s)  "
              f"[{self.get_vote_count()} vote(s)]")
        print("=" * 60)

        for block in self.chain:
            label = "GENESIS" if block.index == 0 else f"Block #{block.index}"
            print(f"\n  [{label}]")
            print(f"    Voter ID      : {block.vote_data.get('voter_id', '?')}")
            print(f"    Vote payload  : {block.vote_data.get('encrypted_vote', '?')[:32]}...")
            print(f"    Stored at     : {block.vote_data.get('timestamp', '?')}")
            print(f"    Block time    : {block.timestamp}")
            print(f"    Prev hash     : {trim(block.prev_hash)}")
            print(f"    This hash     : {trim(block.hash)}")

        print("\n" + "=" * 60)
        status = "[OK]  VALID" if self.is_chain_valid() else "[!!] INVALID -- chain has been tampered with!"
        print(f"  Chain integrity : {status}")
        print("=" * 60)

    def export_chain(self) -> list[dict]:
        """Returns the full chain as a list of dicts (e.g. for JSON serialisation)."""
        return [block.to_dict() for block in self.chain]


# ─────────────────────────────────────────────
#  Quick demo
# ─────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("QUANTUM-SAFE VOTING SYSTEM -- BLOCKCHAIN MODULE (vol4)")
    print("=" * 60)

    bc = Blockchain()

    # Dummy votes — replace encrypted_vote with real PQC ciphertext later
    dummy_votes = [
        ("V001", "a9d72f8c3e1b4a5d6f7e8c9b0a1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d"),
        ("V002", "f1e2d3c4b5a6978869504132fedcba9876543210abcdef0123456789abcdef01"),
        ("V003", "deadbeef0123456789abcdef0123456789abcdef0123456789abcdef01234567"),
    ]

    print("\nAdding dummy votes to the blockchain...\n")
    for voter_id, enc_vote in dummy_votes:
        block = bc.add_vote(voter_id=voter_id, encrypted_vote=enc_vote)
        print(f"  Added: {block}")

    print()
    bc.view_chain()

    # Demonstrate double-vote prevention
    print("\n--- Double-Vote Check ---")
    print(f"Has V001 already voted? {bc.has_voter_voted('V001')}")
    print(f"Has V099 already voted? {bc.has_voter_voted('V099')}")

    # Demonstrate tamper detection
    print("\n--- Tamper Detection ---")
    print("Silently altering Block #1's vote data...")
    bc.chain[1].vote_data["encrypted_vote"] = "tampered_payload_here"
    print(f"Chain valid after tampering? {bc.is_chain_valid()}")
