# vol2_bb84.py
"""
Simulated BB84 Quantum Key Distribution (QKD) Protocol with Finite-Key Security Analysis.

SCOPE, CONTEXT, AND LIMITATIONS:
---------------------------------
This module provides an educational software simulation of the BB84 protocol for the
IPD voting prototype. It models quantum state preparation, measurement, basis sifting,
classical parameter estimation, multi-pass error reconciliation, and universal-hashing
privacy amplification.

IMPORTANT DISCLAIMERS:
1. Software Simulation Only:
   All quantum states and measurements are executed via local circuit simulation (Qiskit Aer).
   This does NOT operate over physical optical fibers or free-space quantum channels and
   does NOT provide physical-layer hardware security guarantees.
2. Model of Classical Channel (Authenticated):
   The classical channel between Aarav (Alice) and Diya (Bob) is modeled in software via
   in-memory variable exchanges. Basis sifting, parameter estimation, multi-pass error reconciliation,
   final-key confirmation tags, and Toeplitz PA seeds ALL use the simulated authenticated channel.
   In real-world QKD, all classical exchanges require
   information-theoretically secure message authentication (such as Wegman-Carter MACs with
   pre-shared keys) to prevent man-in-the-middle attacks.
3. Symmetric Cryptography vs. Post-Quantum:
   Downstream encryption (vol3) uses standard AES-128 via Fernet with HKDF-derived keys.
   This is symmetric cryptography, NOT post-quantum public-key cryptography (e.g., NIST ML-KEM).
   A 256-bit symmetric key provides 128-bit quantum security against Grover's algorithm.
4. Not Production-Ready:
   This prototype demonstrates system architecture and integration; it is not production software.

FINITE-KEY BB84 BOUND (CITED LITERATURE):
-----------------------------------------
The extractable secret key length calculation implements the finite-key analysis for
four-state prepare-and-measure BB84 from:

1. Tomamichel, M., Lim, C. C. W., Gisin, N., & Renner, R. (2012).
   "Tight finite-key analysis for quantum cryptography",
   Nature Communications, 3(1), 634. https://doi.org/10.1038/ncomms1631
   - Secret key length formula: Eq. (1) & (2)
   - Serfling parameter estimation deviation: Eq. (6)
2. Serfling, R. J. (1974).
   "Probability inequalities for the sum in sampling without replacement",
   The Annals of Statistics, 2(1), 39-48. https://doi.org/10.1214/aoms/1177699925
3. Scarani, V., & Renner, R. (2008).
   "Quantum cryptography with finite resources: unconditional security bound for
   discrete-variable protocols with one-way postprocessing",
   Physical Review Letters, 100(20), 200501. https://doi.org/10.1103/PhysRevLett.100.200501

Formulas:
---------
Let n be the number of unrevealed sifted bits kept for raw key generation.
Let m be the number of revealed sample bits used for parameter estimation.
Let Q be the observed sample error rate (sample_error_count / m).
Let eps_pe be the parameter estimation failure probability.
Let eps_pa be the privacy amplification collision probability.
Let eps_s be the smooth min-entropy smoothing parameter.

Serfling deviation for sampling without replacement (Tomamichel et al. 2012, Eq. 6):
    gamma(n, m, eps_pe) = sqrt( ((n + m) * (n + 1) * ln(1 / eps_pe)) / (2 * n^2 * m) )

Phase error rate upper bound:
    e_ph = min(0.5, Q + gamma(n, m, eps_pe))

Binary Shannon entropy:
    h2(p) = -p*log2(p) - (1-p)*log2(1-p) for 0 < p < 0.5; 1.0 for p >= 0.5; 0.0 for p <= 0

Privacy amplification penalty (Tomamichel et al. 2012, Eq. 1):
    Delta_PA = 2 * log2(1 / (2 * eps_pa)) + 2 * log2(1 / eps_s)

Extractable secret key length:
    ell = floor( n * (1 - h2(e_ph)) - leak_EC - Delta_PA )
where leak_EC is the total classical leakage disclosed during error reconciliation
and verification tag confirmation.
"""

import math
import random
import hashlib
from typing import Any
from voting.classical_channel import AuthenticationError, FrameExhaustionError

# pyrefly: ignore[missing-import]
from qiskit import QuantumCircuit
# pyrefly: ignore[missing-import]
from qiskit_aer import AerSimulator


# ── Legacy BB84 Prototype Functions (Maintained for Backward Compatibility) ───

def generate_bb84_key(key_length: int = 8) -> dict:
    """
    Simulates the BB84 quantum key distribution protocol between
    Aarav (sender) and Diya (receiver), and returns their shared key.
    Legacy educational prototype function.
    """
    simulator = AerSimulator()

    aarav_bits = [random.randint(0, 1) for _ in range(key_length)]
    aarav_bases = [random.choice(['+', 'x']) for _ in range(key_length)]
    diya_bases = [random.choice(['+', 'x']) for _ in range(key_length)]

    diya_results = []

    for i in range(key_length):
        qc = QuantumCircuit(1, 1)

        if aarav_bases[i] == '+':
            if aarav_bits[i] == 1:
                qc.x(0)
        else:
            if aarav_bits[i] == 1:
                qc.x(0)
            qc.h(0)

        if diya_bases[i] == 'x':
            qc.h(0)

        qc.measure(0, 0)

        job = simulator.run(qc, shots=1)
        result = job.result()
        counts = result.get_counts()
        measured_bit = int(list(counts.keys())[0])
        diya_results.append(measured_bit)

    shared_key_aarav = []
    shared_key_diya = []

    for i in range(key_length):
        if aarav_bases[i] == diya_bases[i]:
            shared_key_aarav.append(aarav_bits[i])
            shared_key_diya.append(diya_results[i])

    return {
        "aarav_bits": aarav_bits,
        "aarav_bases": aarav_bases,
        "diya_bases": diya_bases,
        "diya_results": diya_results,
        "shared_key_aarav": shared_key_aarav,
        "shared_key_diya": shared_key_diya,
        "keys_match": shared_key_aarav == shared_key_diya,
    }


def get_secure_key(min_length: int = 8) -> list[int]:
    """
    Keeps running BB84 rounds until the shared key reaches at least
    min_length bits. Returns just the final key (list of 0s and 1s),
    trimmed to exactly min_length. Legacy educational prototype function.
    """
    final_key: list[int] = []

    while len(final_key) < min_length:
        result = generate_bb84_key(key_length=16)
        final_key.extend(result["shared_key_aarav"])

    return final_key[:min_length]


# ── Mathematical Helpers & Finite-Key Bound Calculation ───────────────────────

def binary_entropy(p: float) -> float:
    """
    Computes binary Shannon entropy h2(p) = -p*log2(p) - (1-p)*log2(1-p).
    Enforces p in [0, 0.5]; returns 1.0 for p >= 0.5 and 0.0 for p <= 0.0.
    """
    if p <= 0.0:
        return 0.0
    if p >= 0.5:
        return 1.0
    return -p * math.log2(p) - (1.0 - p) * math.log2(1.0 - p)


def compute_finite_key_bound(
    n: int,
    m: int,
    sample_error_count: int,
    reconciliation_disclosed_bits: int,
    eps_pe: float = 1e-10,
    eps_pa: float = 1e-10,
    eps_s: float = 1e-10,
    eps_c: float = 1e-10,
    eps_pa2: float = 0.0,
    eps_s2: float = 0.0,
    eps_auth: float = 0.0,
) -> dict[str, Any]:
    """
    Calculates the extractable secret key length using the finite-key analysis
    from Tomamichel et al. (2012, Nature Communications 3:634) with Serfling (1974)
    sampling without replacement.

    Args:
        n: Number of unrevealed sifted bits kept for raw key generation.
        m: Number of sifted bits revealed for parameter estimation.
        sample_error_count: Number of bit errors observed in the sample of size m.
        reconciliation_disclosed_bits: Total bits disclosed classically during reconciliation & tag.
        eps_pe: Parameter estimation failure probability.
        eps_pa: First privacy amplification collision probability.
        eps_s: Smooth min-entropy smoothing parameter (first PA).
        eps_c: Correctness verification failure probability (e.g. 2^-tag_bits).
        eps_auth: Authentication forgery probability bound.
        eps_pa2: Second privacy amplification collision probability.
        eps_s2: Smooth min-entropy smoothing parameter (second PA).

    Returns:
        dict with key length ell, parameter estimation deviation gamma, phase error bound e_ph,
        PA penalty delta_pa, composed secrecy error eps_sec, correctness error eps_c, auth error eps_auth, and eps_total.
    """
    if n <= 0 or m <= 0:
        return {
            "ell": 0,
            "qber": 0.0,
            "gamma": 1.0,
            "e_ph": 0.5,
            "h_eph": 1.0,
            "reconciliation_disclosed_bits": reconciliation_disclosed_bits,
            "delta_pa": 0.0,
            "eps_sec": 1.0,
            "eps_c": 1.0,
            "eps_auth": 1.0,
            "eps_total": 1.0,
            "valid": False,
        }

    # Reject invalid security-parameter inputs
    if any(e <= 0.0 or e >= 1.0 for e in [eps_pe, eps_pa, eps_s, eps_c]) or (eps_pa2 < 0.0 or eps_pa2 >= 1.0) or (eps_s2 < 0.0 or eps_s2 >= 1.0) or not (0.0 <= eps_auth < 1.0):
        raise ValueError("Security parameters (eps) must be strictly valid.")

    qber = sample_error_count / m

    # Serfling (1974) parameter estimation bound (Tomamichel et al. 2012, Eq. 6)
    # Sampling m bits without replacement from N = n + m total population
    numerator = (n + m) * (n + 1) * math.log(1.0 / eps_pe)
    denominator = 2.0 * (n ** 2) * m
    gamma = math.sqrt(numerator / denominator)

    e_ph = min(0.5, qber + gamma)
    h_eph = binary_entropy(e_ph)

    # Privacy amplification penalty (Tomamichel et al. 2012, Eq. 1)
    delta_pa = 2.0 * math.log2(1.0 / (2.0 * eps_pa)) + 2.0 * math.log2(1.0 / eps_s)

    # Composable secrecy parameter includes first extraction and second extraction (if any)
    eps_sec = eps_pe + 2.0 * eps_s + eps_pa + 2.0 * eps_s2 + eps_pa2

    # Combined total error (secrecy + correctness)
    eps_total = eps_sec + eps_c + eps_auth

    raw_extractable = n * (1.0 - h_eph) - reconciliation_disclosed_bits - delta_pa
    ell = math.floor(raw_extractable)

    return {
        "ell": max(0, ell),
        "raw_extractable": raw_extractable,
        "qber": qber,
        "gamma": gamma,
        "e_ph": e_ph,
        "h_eph": h_eph,
        "reconciliation_disclosed_bits": reconciliation_disclosed_bits,
        "delta_pa": delta_pa,
        "eps_sec": eps_sec,
        "eps_c": eps_c,
        "eps_auth": eps_auth,
        "eps_total": eps_total,
        "valid": ell > 0,
    }


# ── Toeplitz Universal Hash Extractor ─────────────────────────────────────────

def toeplitz_hash(bit_sequence: list[int], output_length: int, matrix_seed: list[int]) -> list[int]:
    """
    Applies Toeplitz matrix multiplication over GF(2) for 2-universal privacy amplification.

    Given an input bit sequence x of length n and target output length l,
    a Toeplitz matrix T of dimension l x n is defined by matrix_seed of length n + l - 1:
        T[i][j] = matrix_seed[j - i + (l - 1)].
    The extracted key vector y is:
        y[i] = (sum_{j=0}^{n-1} T[i][j] * x[j]) mod 2.

    Implemented using efficient integer bitwise operations.
    """
    n = len(bit_sequence)
    l = output_length
    if l <= 0 or n <= 0:
        return []

    expected_seed_len = n + l - 1
    if len(matrix_seed) < expected_seed_len:
        raise ValueError(
            f"Toeplitz seed length must be at least {expected_seed_len}, got {len(matrix_seed)}"
        )

    x_int = 0
    for b in bit_sequence:
        x_int = (x_int << 1) | (1 if b else 0)

    m_int = 0
    for b in matrix_seed[:expected_seed_len]:
        m_int = (m_int << 1) | (1 if b else 0)

    mask = (1 << n) - 1
    total_len = expected_seed_len
    output_bits: list[int] = []

    for i in range(l):
        shift = total_len - (l - 1 - i + n)
        row_int = (m_int >> shift) & mask
        parity = (row_int & x_int).bit_count() % 2
        output_bits.append(parity)

    return output_bits


# ── Multi-Pass Cascade-Style Key Reconciliation ───────────────────────────────

def reconcile_keys(
    alice_bits: list[int],
    bob_bits: list[int],
    block_size: int = 48,
    tag_bits: int = 32,
    num_passes: int = 3,
    sim_alice: Any = None,
    sim_bob: Any = None,
) -> tuple[list[int], list[int], int, bool]:
    """
    Models classical error reconciliation and verification between Alice and Bob
    using multi-pass block parity with bisection and deterministic permutations (Cascade style).

    Handles multiple errors occurring in the same block across successive permutation passes
    by queuing and revisiting earlier blocks that become unbalanced when an error is corrected.
    Tracks every classical bit disclosed during parity exchanges and confirmation tags.
    Checks tag after each pass so clean channels terminate without unnecessary leakage.

    Model Note: Initial block-parity exchanges, bisection queries, verification tags,
    and revisit parities/bisections are authenticated via the provided sim_alice/sim_bob endpoints.
    The simulated protocol decision is driven solely by tag equality.
    Limitation: The verification tag is constructed via truncated SHA-256. While practically
    unforgeable (random oracle heuristic), a strict information-theoretic composable bound
    would require an eps-almost 2-universal hash family (e.g., polynomial evaluation over GF(2^t)).
    Thus, eps_c = 2^-32 serves here as an educational estimate for the correctness bound.
    Frame Budget (Estimate only): The conservative maximum Z-key input is 3,999 bits, giving at most
    334 blocks across the three passes. This yields 668 initial-parity frames, 3,508 main-bisection frames,
    and a bounded revisit queue (capped at 34 revisits, each up to 6 bisections + 1 parity, 476 revisit frames),
    and up to 6 verification-tag frames across the three passes. Including parameter
    estimation, sifting, the first PA seed exchange (2 frames), final key confirmation (2 frames),
    and the second PA seed exchange (2 frames), the projected total is 4,696 frames, well within the 4,700 limit.

    Returns:
        (alice_reconciled, bob_reconciled, disclosed_bits, success)
    """
    alice = list(alice_bits)
    bob = list(bob_bits)
    n = len(alice)
    disclosed_bits = 0

    if (sim_alice is None) != (sim_bob is None):
        raise ValueError("Both sim_alice and sim_bob must be provided together, or both omitted.")

    if n == 0 or len(bob) != n:
        return alice, bob, 0, False

    rng_perm = random.Random(1337)
    tag_bytes_len = max(1, tag_bits // 8)

    def _compute_tag(bits: list[int]) -> bytes:
        byte_arr = bytearray()
        for i in range(0, len(bits), 8):
            val = 0
            for b in bits[i : i + 8]:
                val = (val << 1) | (1 if b else 0)
            byte_arr.append(val)
        return hashlib.sha256(byte_arr).digest()[:tag_bytes_len]

    def _bisect_block(blk: list[int]) -> int:
        nonlocal disclosed_bits
        lo, hi = 0, len(blk)
        while (hi - lo) > 1:
            mid = (lo + hi) // 2
            left_indices = blk[lo:mid]
            p_a_left_local = sum(alice[i] for i in left_indices) % 2
            p_b_left_local = sum(bob[i] for i in left_indices) % 2
            disclosed_bits += 1

            if sim_alice is not None and sim_bob is not None:
                frame_a = sim_alice.send_frame("bisection_parity", {"p": p_a_left_local})
                frame_b = sim_bob.send_frame("bisection_parity", {"p": p_b_left_local})
                rx_a = sim_bob.receive_frame(frame_a, "bisection_parity")
                rx_b = sim_alice.receive_frame(frame_b, "bisection_parity")
                if type(rx_a.get("p")) is not int or rx_a["p"] not in (0, 1):
                    raise AuthenticationError("Invalid parity")
                if type(rx_b.get("p")) is not int or rx_b["p"] not in (0, 1):
                    raise AuthenticationError("Invalid parity")
                p_a_left = rx_a["p"]
                p_b_left = rx_b["p"]
            else:
                p_a_left = p_a_left_local
                p_b_left = p_b_left_local

            if p_a_left != p_b_left:
                hi = mid
            else:
                lo = mid

        err_idx = blk[lo]
        bob[err_idx] = 1 - bob[err_idx]
        return err_idx

    # Pre-generate blocks for all passes
    blocks_by_pass = []
    for pass_idx in range(num_passes):
        if pass_idx == 0:
            indices = list(range(n))
            b_size = block_size
        elif pass_idx == 1:
            indices = rng_perm.sample(range(n), n)
            b_size = block_size
        else:
            indices = rng_perm.sample(range(n), n)
            b_size = int(block_size * 1.5)

        pass_blocks = []
        num_blocks = (n + b_size - 1) // b_size
        for b in range(num_blocks):
            blk = indices[b * b_size : min(n, (b + 1) * b_size)]
            if blk:
                pass_blocks.append(blk)
        blocks_by_pass.append(pass_blocks)

    revisit_queue = []
    revisit_budget = MAX_RECONCILIATION_REVISITS

    def queue_revisits(err_idx: int, up_to_pass: int):
        for prev_p in range(up_to_pass):
            for prev_blk in blocks_by_pass[prev_p]:
                if err_idx in prev_blk:
                    # Avoid adding duplicates
                    item = (prev_p, prev_blk)
                    if item not in revisit_queue:
                        revisit_queue.append(item)
                    break

    for pass_idx in range(num_passes):
        for blk_indices in blocks_by_pass[pass_idx]:
            p_alice_local = sum(alice[i] for i in blk_indices) % 2
            p_bob_local = sum(bob[i] for i in blk_indices) % 2
            disclosed_bits += 1

            if sim_alice is not None and sim_bob is not None:
                frame_a = sim_alice.send_frame("parity", {"p": p_alice_local})
                frame_b = sim_bob.send_frame("parity", {"p": p_bob_local})
                rx_a = sim_bob.receive_frame(frame_a, "parity")
                rx_b = sim_alice.receive_frame(frame_b, "parity")
                if type(rx_a.get("p")) is not int or rx_a["p"] not in (0, 1):
                    raise AuthenticationError("Invalid parity")
                if type(rx_b.get("p")) is not int or rx_b["p"] not in (0, 1):
                    raise AuthenticationError("Invalid parity")
                p_a = rx_a["p"]
                p_b = rx_b["p"]
            else:
                p_a = p_alice_local
                p_b = p_bob_local

            if p_a != p_b:
                err_idx = _bisect_block(blk_indices)
                queue_revisits(err_idx, pass_idx)

                # Process revisits
                while revisit_queue and revisit_budget > 0:
                    rp, r_blk = revisit_queue.pop(0)
                    revisit_budget -= 1

                    rp_a_local = sum(alice[i] for i in r_blk) % 2
                    rp_b_local = sum(bob[i] for i in r_blk) % 2
                    disclosed_bits += 1

                    if sim_alice is not None and sim_bob is not None:
                        f_a = sim_alice.send_frame("revisit_parity", {"p": rp_a_local})
                        f_b = sim_bob.send_frame("revisit_parity", {"p": rp_b_local})
                        r_a = sim_bob.receive_frame(f_a, "revisit_parity")
                        r_b = sim_alice.receive_frame(f_b, "revisit_parity")
                        if type(r_a.get("p")) is not int or r_a["p"] not in (0, 1):
                            raise AuthenticationError("Invalid parity")
                        if type(r_b.get("p")) is not int or r_b["p"] not in (0, 1):
                            raise AuthenticationError("Invalid parity")
                        rp_a = r_a["p"]
                        rp_b = r_b["p"]
                    else:
                        rp_a = rp_a_local
                        rp_b = rp_b_local

                    if rp_a != rp_b:
                        err_idx2 = _bisect_block(r_blk)
                        # Queue all blocks from passes up to current pass_idx (except rp) containing err_idx2
                        for other_p in range(pass_idx + 1):
                            if other_p == rp:
                                continue
                            for other_blk in blocks_by_pass[other_p]:
                                if err_idx2 in other_blk:
                                    item = (other_p, other_blk)
                                    if item not in revisit_queue:
                                        revisit_queue.append(item)
                                    break

        # Verification / Confirmation tag check after this pass
        alice_tag_local = _compute_tag(alice)
        bob_tag_local = _compute_tag(bob)
        disclosed_bits += tag_bits

        if sim_alice is not None and sim_bob is not None:
            frame_a = sim_alice.send_frame("verification_tag", {"tag": alice_tag_local.hex()})
            frame_b = sim_bob.send_frame("verification_tag", {"tag": bob_tag_local.hex()})
            rx_a = sim_bob.receive_frame(frame_a, "verification_tag")
            rx_b = sim_alice.receive_frame(frame_b, "verification_tag")
            if not isinstance(rx_a.get("tag"), str) or len(rx_a["tag"]) != tag_bytes_len * 2:
                raise AuthenticationError("Invalid tag")
            if not isinstance(rx_b.get("tag"), str) or len(rx_b["tag"]) != tag_bytes_len * 2:
                raise AuthenticationError("Invalid tag")
            try:
                alice_tag = bytes.fromhex(rx_a["tag"])
                bob_tag = bytes.fromhex(rx_b["tag"])
            except ValueError:
                raise AuthenticationError("Invalid tag hex")
        else:
            alice_tag = alice_tag_local
            bob_tag = bob_tag_local

        if alice_tag == bob_tag:
            return alice, bob, disclosed_bits, True

    return alice, bob, disclosed_bits, False

# ── Constants for Conservative Frame-Budget Bounds ────────────────────────
# Note: 4,700 frames is a conservative future framed-channel design estimate.
# Assumptions: at most 15 rounds, batches capped at 4,000 bits, three
# reconciliation passes with block sizes 32/32/48, and up to 34 revisits.
# This bound includes basis exchange, parameter estimation, reconciliation,
# PA seeds, and confirmation tags exchanged over the simulated authenticated
# channel.
MAX_RECONCILIATION_REVISITS = 34
TARGET_SIFTED_BITS = 4000
MAX_BATCH_SIZE = 4000
MAX_ROUNDS_LIMIT = 15
REC_NUM_PASSES = 3
REC_BLOCK_SIZE = 32

def align_and_cap_sifted_arrays(
    sifted_a: list[int], sifted_b: list[int], bases: list[str], limit: int
) -> tuple[list[int], list[int], list[str]]:
    if not (len(sifted_a) == len(sifted_b) == len(bases)):
        raise ValueError("Mismatched input lengths for sifted arrays.")
    return sifted_a[:limit], sifted_b[:limit], bases[:limit]

# ── M4 Secure BB84 Protocol Simulation ────────────────────────────────────────

def run_secure_bb84(
    min_key_length: int = 256,
    eavesdrop: bool = False,
    channel_error_rate: float = 0.0,
    sample_ratio: float = 0.25,
    qber_threshold: float = 0.11,
    eps_pe: float = 1e-10,
    eps_pa: float = 1e-10,
    eps_s: float = 1e-10,
    seed: int | None = None,
    max_rounds: int = MAX_ROUNDS_LIMIT,
    auth_key: bytes | None = None,
) -> dict[str, Any]:
    """
    Simulates a four-state prepare-and-measure BB84 protocol session with finite-key bounds:
    1. Multi-round quantum transmission with basis sifting (chunked for fast execution).
    2. Optional channel error noise and intercept-resend eavesdropping.
    3. Parameter estimation uses ALL X-basis bits. The sample_ratio parameter is
       retained for compatibility and validated to be in (0, 1), but its value
       does not control the sampling size.
    4. Threshold verification (aborts if QBER > qber_threshold).
    5. Multi-pass key reconciliation with exact classical leakage tracking and tag confirmation.
    6. Published finite-key bound calculation (Tomamichel et al. 2012) bounding phase error
       via Serfling (1974) parameter estimation.
    7. Two-stage Privacy Amplification (PA): First stage extracts an intermediate key (e.g., 419 bits).
       Then, an authenticated final-key confirmation exposes a 32-bit universal tag.
       Finally, a second PA stage compresses the remaining conditional min-entropy into the final application key.
    8. Composable Secrecy Calculation: The secrecy bound is hybrid, explicitly calculating the first extraction error,
       at-most-32-bit confirmation leakage, and second extraction error. Correctness error is independent, derived
       exclusively from the final confirmation tag.
    9. Enforces sufficient raw key bound (ell_max) to support the required intermediate key length.
    10. OS-backed randomness by default (via random.SystemRandom); deterministic PRNG only when seeded.
    """
    # ── Composable Security Accounting ────────────────────────────────────────
    # The authentication error (eps_auth) bounds the probability of a successful forgery,
    # strictly conditional on the Wegman-Carter MAC over GF(2^128) using polynomial evaluation,
    # uniform keys, unique pads per frame, and the enforced channel limits.
    # Derivation:
    # 1. For a message of L blocks, the forgery probability is L / 2^128.
    # 2. The channel enforces MAX_SERIALIZED_FRAME_BYTES. With padding and a 16-byte length block,
    #    the polynomial evaluation processes at most L_{max} = ceil(MAX_SERIALIZED_FRAME_BYTES / 16) + 1 blocks.
    # 3. The protocol exchanges at most MAX_FRAMES frames. The receiver aborts immediately
    #    upon the first failure, limiting the adversary to 1 attempt per expected message.
    # 4. By union bound, eps_auth <= MAX_FRAMES * L_{max} * 2^-128.
    # Note: This is an educational estimate for the MAC bound, not an independently verified composable guarantee.
    import math
    from voting.classical_channel import MAX_FRAMES, MAX_SERIALIZED_FRAME_BYTES
    l_max = math.ceil(MAX_SERIALIZED_FRAME_BYTES / 16) + 1
    eps_auth = MAX_FRAMES * l_max * (2.0 ** -128)

    is_valid_type = type(channel_error_rate) in (int, float) and not isinstance(channel_error_rate, bool)
    if not is_valid_type or not (0.0 <= channel_error_rate and channel_error_rate * 1.5 <= 1.0):
        return {
            "secure": False,
            "aborted": True,
            "qber": 0.0,
            "sample_size": 0,
            "error_count": 0,
            "qber_threshold": qber_threshold,
            "sifted_key_length": 0,
            "eps_auth": eps_auth,
            "final_key": None,
            "final_key_length": 0,
            "eavesdrop": eavesdrop,
            "sample_indices": [],
            "remaining_indices": [],
            "reconciliation_disclosed_bits": 0,
            "keys_match": False,
            "reason": "invalid_channel_error_rate",
        }

    if auth_key is not None:
        if not isinstance(auth_key, bytes):
            return {
                "secure": False,
                "aborted": True,
                "qber": 0.0,
                "sample_size": 0,
                "error_count": 0,
                "qber_threshold": qber_threshold,
                "sifted_key_length": 0,
                "eps_auth": eps_auth,
                "final_key": None,
                "final_key_length": 0,
                "eavesdrop": eavesdrop,
                "sample_indices": [],
                "remaining_indices": [],
                "reconciliation_disclosed_bits": 0,
                "keys_match": False,
                "reason": "invalid_auth_key_type",
            }
        if len(auth_key) < 16 + MAX_FRAMES * 16:
            return {
                "secure": False,
                "aborted": True,
                "qber": 0.0,
                "sample_size": 0,
                "error_count": 0,
                "qber_threshold": qber_threshold,
                "sifted_key_length": 0,
                "eps_auth": eps_auth,
                "final_key": None,
                "final_key_length": 0,
                "eavesdrop": eavesdrop,
                "sample_indices": [],
                "remaining_indices": [],
                "reconciliation_disclosed_bits": 0,
                "keys_match": False,
                "reason": "invalid_auth_key_length",
            }

    if min_key_length < 256:
        return {
            "secure": False,
            "aborted": True,
            "qber": 0.0,
            "sample_size": 0,
            "error_count": 0,
            "qber_threshold": qber_threshold,
            "sifted_key_length": 0,
            "eps_auth": eps_auth,
            "final_key": None,
            "final_key_length": 0,
            "eavesdrop": eavesdrop,
            "sample_indices": [],
            "remaining_indices": [],
            "reconciliation_disclosed_bits": 0,
            "keys_match": False,
            "reason": "min_key_length_must_be_at_least_256",
        }

    if sample_ratio <= 0.0 or sample_ratio >= 1.0 or max_rounds <= 0 or qber_threshold <= 0.0:
        return {
            "secure": False,
            "aborted": True,
            "qber": 0.0,
            "sample_size": 0,
            "error_count": 0,
            "qber_threshold": qber_threshold,
            "sifted_key_length": 0,
            "eps_auth": eps_auth,
            "final_key": None,
            "final_key_length": 0,
            "eavesdrop": eavesdrop,
            "sample_indices": [],
            "remaining_indices": [],
            "reconciliation_disclosed_bits": 0,
            "keys_match": False,
            "reason": "invalid_parameters",
        }

    if max_rounds > MAX_ROUNDS_LIMIT:
        return {
            "secure": False,
            "aborted": True,
            "qber": 0.0,
            "sample_size": 0,
            "error_count": 0,
            "qber_threshold": qber_threshold,
            "sifted_key_length": 0,
            "eps_auth": eps_auth,
            "final_key": None,
            "final_key_length": 0,
            "eavesdrop": eavesdrop,
            "sample_indices": [],
            "remaining_indices": [],
            "reconciliation_disclosed_bits": 0,
            "keys_match": False,
            "reason": "max_rounds_exceeds_limit",
        }

    if seed is not None:
        rng = random.Random(seed)
    else:
        rng = random.SystemRandom()

    simulator = AerSimulator()

    # To satisfy the Tomamichel et al. (2012) finite-key bound with Serfling parameter estimation
    # and achieve ell >= 256 bits when using only the Z-basis for key, we target at least TARGET_SIFTED_BITS bits.
    target_sifted_needed = TARGET_SIFTED_BITS
    chunk_size = 50

    sifted_aarav: list[int] = []
    sifted_diya: list[int] = []
    sifted_bases: list[str] = []

    # ── Transmission & Sifting Loop ───────────────────────────────────────────
    for round_idx in range(max_rounds):
        needed_sifted = target_sifted_needed - len(sifted_aarav)
        batch_size = max(500, min(MAX_BATCH_SIZE, int(2.1 * needed_sifted)))

        aarav_bits = [rng.randint(0, 1) for _ in range(batch_size)]
        aarav_bases = [rng.choice(['+', 'x']) for _ in range(batch_size)]
        diya_bases = [rng.choice(['+', 'x']) for _ in range(batch_size)]

        if not eavesdrop:
            circuits: list[QuantumCircuit] = []
            for c_start in range(0, batch_size, chunk_size):
                c_len = min(chunk_size, batch_size - c_start)
                qc = QuantumCircuit(c_len, c_len)
                for q in range(c_len):
                    idx = c_start + q
                    bit = aarav_bits[idx]
                    if aarav_bases[idx] == '+':
                        if bit == 1:
                            qc.x(q)
                    else:
                        if bit == 1:
                            qc.x(q)
                        qc.h(q)

                    # Optional channel Pauli noise
                    # Scaled by 1.5 so that the effective QBER (2/3 of the Pauli errors) equals channel_error_rate
                    if channel_error_rate > 0.0 and rng.random() < channel_error_rate * 1.5:
                        pauli = rng.choice(['x', 'y', 'z'])
                        if pauli == 'x':
                            qc.x(q)
                        elif pauli == 'y':
                            qc.y(q)
                        elif pauli == 'z':
                            qc.z(q)

                    if diya_bases[idx] == 'x':
                        qc.h(q)
                    qc.measure(q, q)
                circuits.append(qc)

            sim_seed = rng.randint(1, 10_000_000) if seed is not None else None
            job = simulator.run(circuits, shots=1, seed_simulator=sim_seed)
            results = job.result().get_counts()
            if isinstance(results, dict):
                results = [results]

            diya_results = []
            for counts, c_start in zip(results, range(0, batch_size, chunk_size)):
                c_len = min(chunk_size, batch_size - c_start)
                bitstr = list(counts.keys())[0]
                rev = list(reversed(bitstr))
                diya_results.extend(int(rev[q]) for q in range(c_len))

        else:
            # Genuine intercept-resend attack: Eve intercepts, measures, and re-prepares
            eve_bases = [rng.choice(['+', 'x']) for _ in range(batch_size)]
            circuits = []
            for c_start in range(0, batch_size, chunk_size):
                c_len = min(chunk_size, batch_size - c_start)
                qc = QuantumCircuit(c_len, 2 * c_len)
                for q in range(c_len):
                    idx = c_start + q
                    if aarav_bases[idx] == '+':
                        if aarav_bits[idx] == 1:
                            qc.x(q)
                    else:
                        if aarav_bits[idx] == 1:
                            qc.x(q)
                        qc.h(q)

                    # Eve intercepts and measures
                    if eve_bases[idx] == 'x':
                        qc.h(q)
                    qc.measure(q, q)

                    # Eve re-prepares
                    if eve_bases[idx] == 'x':
                        qc.h(q)

                    # Optional channel Pauli noise
                    # Scaled by 1.5 so that the effective QBER (2/3 of the Pauli errors) equals channel_error_rate
                    if channel_error_rate > 0.0 and rng.random() < channel_error_rate * 1.5:
                        pauli = rng.choice(['x', 'y', 'z'])
                        if pauli == 'x':
                            qc.x(q)
                        elif pauli == 'y':
                            qc.y(q)
                        elif pauli == 'z':
                            qc.z(q)

                    # Diya measures
                    if diya_bases[idx] == 'x':
                        qc.h(q)
                    qc.measure(q, c_len + q)
                circuits.append(qc)

            sim_seed = rng.randint(1, 10_000_000) if seed is not None else None
            job = simulator.run(circuits, shots=1, seed_simulator=sim_seed)
            results = job.result().get_counts()
            if isinstance(results, dict):
                results = [results]

            diya_results = []
            for counts, c_start in zip(results, range(0, batch_size, chunk_size)):
                c_len = min(chunk_size, batch_size - c_start)
                bitstr = list(counts.keys())[0]
                rev = list(reversed(bitstr))
                diya_results.extend(int(rev[c_len + q]) for q in range(c_len))

        # Sifting
        try:
            from voting.classical_channel import SynchronizedFrameAllocator, AuthenticatedChannelEndpoint

            # Setup simulation-only channel if not already created for this run
            if "sim_allocator" not in locals():
                if auth_key is not None:
                    sim_auth_key = auth_key
                else:
                    import os
                    auth_key_len = 16 + MAX_FRAMES * 16
                    # Centrally generated simulation material for educational purposes,
                    # NOT a deployed pre-shared secret.
                    sim_auth_key = os.urandom(auth_key_len)
                sim_allocator = SynchronizedFrameAllocator(sim_auth_key)
                sim_alice = AuthenticatedChannelEndpoint("sim-run", "alice", "bob", sim_allocator)
                sim_bob = AuthenticatedChannelEndpoint("sim-run", "bob", "alice", sim_allocator)

            # Authentic Basis Exchange
            frame_a = sim_alice.send_frame("basis_exchange", {"bases": aarav_bases})
            frame_b = sim_bob.send_frame("basis_exchange", {"bases": diya_bases})

            rx_aarav_payload = sim_bob.receive_frame(frame_a, "basis_exchange")
            rx_diya_payload = sim_alice.receive_frame(frame_b, "basis_exchange")

            rx_aarav_bases = rx_aarav_payload.get("bases")
            rx_diya_bases = rx_diya_payload.get("bases")

            if not isinstance(rx_aarav_bases, list) or len(rx_aarav_bases) != batch_size or any(b not in ('+', 'x') for b in rx_aarav_bases):
                raise AuthenticationError("Invalid bases payload")
            if not isinstance(rx_diya_bases, list) or len(rx_diya_bases) != batch_size or any(b not in ('+', 'x') for b in rx_diya_bases):
                raise AuthenticationError("Invalid bases payload")
        except Exception as e:
            return {
                "secure": False,
                "aborted": True,
                "qber": 0.0,
                "sample_size": 0,
                "error_count": 0,
                "qber_threshold": qber_threshold,
                "sifted_key_length": 0,
                "eps_auth": eps_auth,
            "final_key": None,
                "final_key_length": 0,
                "eavesdrop": eavesdrop,
                "sample_indices": [],
                "remaining_indices": [],
                "reconciliation_disclosed_bits": 0,
                "keys_match": False,
                "reason": f"authentication_failed",
            }

        for i in range(batch_size):
            if rx_aarav_bases[i] == rx_diya_bases[i]:
                sifted_aarav.append(aarav_bits[i])
                sifted_diya.append(diya_results[i])
                sifted_bases.append(rx_aarav_bases[i])

        if len(sifted_aarav) >= target_sifted_needed:
            break

    sifted_aarav, sifted_diya, sifted_bases = align_and_cap_sifted_arrays(
        sifted_aarav, sifted_diya, sifted_bases, TARGET_SIFTED_BITS
    )
    total_sifted = len(sifted_aarav)

    # ── Basis-Specific Parameter Estimation (Tomamichel et al. 2012) ──────────
    # Proof mapping: We apply the finite-key proof for asymmetric BB84 where the
    # Z-basis ('+') is used strictly for key generation (n bits) and the X-basis ('x')
    # is used strictly for parameter estimation (m bits). This satisfies the requirement
    # that phase error in the Z-basis key is bounded by the bit error in the X-basis.

    z_indices = [i for i, b in enumerate(sifted_bases) if b == '+']
    x_indices = [i for i, b in enumerate(sifted_bases) if b == 'x']

    sample_size = len(x_indices)
    remaining_count = len(z_indices)

    if remaining_count < min_key_length or sample_size == 0:
        return {
            "secure": False,
            "aborted": True,
            "qber": 0.0,
            "sample_size": sample_size,
            "error_count": 0,
            "qber_threshold": qber_threshold,
            "sifted_key_length": total_sifted,
            "eps_auth": eps_auth,
            "final_key": None,
            "final_key_length": 0,
            "eavesdrop": eavesdrop,
            "sample_indices": [],
            "remaining_indices": [],
            "reconciliation_disclosed_bits": 0,
            "keys_match": False,
            "reason": "insufficient_sifted_bits",
        }

    # ── Sample Selection & QBER Estimation ────────────────────────────────────
    sample_indices = sorted(rng.sample(x_indices, sample_size))
    sample_indices_set = set(sample_indices)

    aarav_sample_bits = [sifted_aarav[idx] for idx in sample_indices]
    diya_sample_bits = [sifted_diya[idx] for idx in sample_indices]

    try:
        # Authentic Parameter Estimation Exchange (Software simulation only)
        # Does NOT provide physical-layer QKD security.
        frame_a_pe = sim_alice.send_frame("parameter_estimation", {
            "sample_indices": sample_indices,
            "sample_bits": aarav_sample_bits
        })
        frame_b_pe = sim_bob.send_frame("parameter_estimation", {
            "sample_indices": sample_indices, # Bob echoes indices for protocol symmetry
            "sample_bits": diya_sample_bits
        })

        rx_aarav_pe = sim_bob.receive_frame(frame_a_pe, "parameter_estimation")
        rx_diya_pe = sim_alice.receive_frame(frame_b_pe, "parameter_estimation")

        if not isinstance(rx_aarav_pe.get("sample_indices"), list) or not isinstance(rx_diya_pe.get("sample_indices"), list):
            raise AuthenticationError("Invalid sample indices format")

        # Verify both endpoints received the exactly agreed-upon sample indices
        if rx_aarav_pe["sample_indices"] != sample_indices or rx_diya_pe["sample_indices"] != sample_indices:
            raise AuthenticationError("Mismatched sample indices received")

        rx_aarav_bits = rx_aarav_pe.get("sample_bits")
        rx_diya_bits = rx_diya_pe.get("sample_bits")

        if not isinstance(rx_aarav_bits, list) or not isinstance(rx_diya_bits, list):
            raise AuthenticationError("Invalid sample bits format")
        # Verify payload shapes and bit domains
        if len(rx_aarav_bits) != sample_size or len(rx_diya_bits) != sample_size:
            raise AuthenticationError("Mismatched sample bits length")
        if any(type(b) is not int or b not in (0, 1) for b in rx_aarav_bits) or any(type(b) is not int or b not in (0, 1) for b in rx_diya_bits):
            raise AuthenticationError("Malformed sample bits")

    except Exception as e:
        return {
            "secure": False,
            "aborted": True,
            "qber": 0.0,
            "sample_size": sample_size,
            "error_count": 0,
            "qber_threshold": qber_threshold,
            "sifted_key_length": total_sifted,
            "eps_auth": eps_auth,
            "final_key": None,
            "final_key_length": 0,
            "eavesdrop": eavesdrop,
            "sample_indices": sample_indices,
            "remaining_indices": [],
            "reconciliation_disclosed_bits": 0,
            "keys_match": False,
            "reason": "authentication_failed",
        }

    # In a real distributed protocol, both Alice and Bob independently calculate
    # the QBER comparing their local bits to the verified received remote bits,
    # and separately abort if the threshold is exceeded. In this centralized
    # simulation, we functionally replicate this by calculating QBER once using
    # Alice's verified received bits (rx_aarav_bits) against Bob's local bits
    # (diya_sample_bits).
    error_count = sum(
        1 for i in range(sample_size) if rx_aarav_bits[i] != diya_sample_bits[i]
    )
    qber = error_count / sample_size

    # STRICT EXCLUSION: Key is generated ONLY from the Z-basis ('+') bits.
    remaining_indices = z_indices
    raw_aarav = [sifted_aarav[idx] for idx in remaining_indices]
    raw_diya = [sifted_diya[idx] for idx in remaining_indices]

    # QBER threshold check
    if qber > qber_threshold:
        return {
            "secure": False,
            "aborted": True,
            "qber": qber,
            "sample_size": sample_size,
            "error_count": error_count,
            "qber_threshold": qber_threshold,
            "sifted_key_length": total_sifted,
            "eps_auth": eps_auth,
            "final_key": None,
            "final_key_length": 0,
            "eavesdrop": eavesdrop,
            "sample_indices": sample_indices,
            "remaining_indices": remaining_indices,
            "reconciliation_disclosed_bits": 0,
            "keys_match": False,
            "reason": "qber_threshold_exceeded",
        }

    # ── Key Reconciliation & Verification ─────────────────────────────────────
    tag_bits = 32


    eps_c = 2.0 ** (-tag_bits)  # Strictly justified by final universal Toeplitz hash confirmation

    try:
        rec_aarav, rec_diya, leak_ec, rec_success = reconcile_keys(
            alice_bits=raw_aarav,
            bob_bits=raw_diya,
            block_size=REC_BLOCK_SIZE,
            tag_bits=tag_bits,
            num_passes=REC_NUM_PASSES,
            sim_alice=sim_alice,
            sim_bob=sim_bob,
        )
    except AuthenticationError:
        return {
            "secure": False,
            "aborted": True,
            "qber": qber,
            "sample_size": sample_size,
            "error_count": error_count,
            "qber_threshold": qber_threshold,
            "sifted_key_length": total_sifted,
            "eps_auth": eps_auth,
            "final_key": None,
            "final_key_length": 0,
            "eavesdrop": eavesdrop,
            "sample_indices": sample_indices,
            "remaining_indices": remaining_indices,
            "reconciliation_disclosed_bits": 0,
            "keys_match": False,
            "reason": "authentication_failed",
        }
    except FrameExhaustionError:
        return {
            "secure": False,
            "aborted": True,
            "qber": qber,
            "sample_size": sample_size,
            "error_count": error_count,
            "qber_threshold": qber_threshold,
            "sifted_key_length": total_sifted,
            "eps_auth": eps_auth,
            "final_key": None,
            "final_key_length": 0,
            "eavesdrop": eavesdrop,
            "sample_indices": sample_indices,
            "remaining_indices": remaining_indices,
            "reconciliation_disclosed_bits": 0,
            "keys_match": False,
            "reason": "frame_budget_exceeded",
        }

    if not rec_success:
        return {
            "secure": False,
            "aborted": True,
            "qber": qber,
            "sample_size": sample_size,
            "error_count": error_count,
            "qber_threshold": qber_threshold,
            "sifted_key_length": total_sifted,
            "eps_auth": eps_auth,
            "final_key": None,
            "final_key_length": 0,
            "eavesdrop": eavesdrop,
            "sample_indices": sample_indices,
            "remaining_indices": remaining_indices,
            "reconciliation_disclosed_bits": leak_ec,
            "keys_match": False,
            "reason": "reconciliation_verification_failed",
        }

    # ── Published Finite-Key Bound Evaluation ─────────────────────────────────
    bound_result = compute_finite_key_bound(
        n=len(rec_aarav),
        m=sample_size,
        sample_error_count=error_count,
        reconciliation_disclosed_bits=leak_ec,
        eps_pe=eps_pe,
        eps_pa=eps_pa,
        eps_s=eps_s,
        eps_c=eps_c,
        eps_pa2=eps_pa,
        eps_s2=eps_s,
        eps_auth=eps_auth,
    )

    ell_max = bound_result["ell"]

    # Calculate second extraction penalty (Delta_pa2)
    delta_pa2 = 2.0 * math.log2(1.0 / (2.0 * eps_pa)) + 2.0 * math.log2(1.0 / eps_s)
    first_pa_output_length = min_key_length + tag_bits + math.ceil(delta_pa2)

    if ell_max < first_pa_output_length:
        return {
            "secure": False,
            "aborted": True,
            "qber": qber,
            "sample_size": sample_size,
            "error_count": error_count,
            "qber_threshold": qber_threshold,
            "sifted_key_length": total_sifted,
            "eps_auth": eps_auth,
            "final_key": None,
            "final_key_length": 0,
            "eavesdrop": eavesdrop,
            "sample_indices": sample_indices,
            "remaining_indices": remaining_indices,
            "reconciliation_disclosed_bits": leak_ec,
            "reconciled_key_length": len(rec_aarav),
            "finite_key_bound": bound_result,
            "keys_match": True,
            "reason": "finite_key_bound_insufficient",
        }

    # ── Two-Stage Privacy Amplification & Key Confirmation ────────────────────

    # Stage 1: Extract intermediate key with sufficient entropy for final tag + key
    seed_length_1 = len(rec_aarav) + first_pa_output_length - 1
    toeplitz_seed_1_aarav = [rng.randint(0, 1) for _ in range(seed_length_1)]

    try:
        if sim_alice is not None and sim_bob is not None:
            # 1. First PA Seed Exchange
            frame_a_seed1 = sim_alice.send_frame("toeplitz_seed", {"seed": toeplitz_seed_1_aarav})
            rx_diya_seed1 = sim_bob.receive_frame(frame_a_seed1, "toeplitz_seed")
            toeplitz_seed_1_diya = rx_diya_seed1.get("seed")

            if not isinstance(toeplitz_seed_1_diya, list) or len(toeplitz_seed_1_diya) != seed_length_1 or any(type(b) is not int or b not in (0, 1) for b in toeplitz_seed_1_diya):
                raise AuthenticationError("Invalid Toeplitz seed format received.")

            frame_b_ack1 = sim_bob.send_frame("toeplitz_seed_ack", {"status": "ok"})
            rx_ack1_payload = sim_alice.receive_frame(frame_b_ack1, "toeplitz_seed_ack")
            if rx_ack1_payload.get("status") != "ok":
                raise AuthenticationError("Invalid acknowledgment status.")

            # Apply Stage 1 PA
            pa1_out_aarav = toeplitz_hash(rec_aarav, first_pa_output_length, toeplitz_seed_1_aarav)
            pa1_out_diya = toeplitz_hash(rec_diya, first_pa_output_length, toeplitz_seed_1_diya)

            # 2. Final Key Confirmation
            conf_seed_len = first_pa_output_length + tag_bits - 1
            conf_seed_aarav = [rng.randint(0, 1) for _ in range(conf_seed_len)]
            conf_tag_aarav = toeplitz_hash(pa1_out_aarav, tag_bits, conf_seed_aarav)

            frame_a_conf = sim_alice.send_frame("key_confirmation", {"seed": conf_seed_aarav, "tag": conf_tag_aarav})
            rx_b_conf = sim_bob.receive_frame(frame_a_conf, "key_confirmation")

            conf_seed_diya = rx_b_conf.get("seed")
            received_tag = rx_b_conf.get("tag")
            if not isinstance(conf_seed_diya, list) or len(conf_seed_diya) != conf_seed_len or any(type(b) is not int or b not in (0, 1) for b in conf_seed_diya):
                raise AuthenticationError("Invalid conf_seed format")
            if not isinstance(received_tag, list) or len(received_tag) != tag_bits or any(type(b) is not int or b not in (0, 1) for b in received_tag):
                raise AuthenticationError("Invalid received_tag format")

            conf_tag_diya = toeplitz_hash(pa1_out_diya, tag_bits, conf_seed_diya)
            match_status = "match" if conf_tag_diya == received_tag else "mismatch"

            frame_b_conf_ack = sim_bob.send_frame("key_confirmation_ack", {"status": match_status})

            rx_ack2_payload = sim_alice.receive_frame(frame_b_conf_ack, "key_confirmation_ack")

            if rx_ack2_payload.get("status") not in ("match", "mismatch"):
                raise AuthenticationError("Invalid confirmation ack status.")

            if rx_ack2_payload.get("status") != "match":
                return {
                    "secure": False,
                    "aborted": True,
                    "qber": qber,
                    "sample_size": sample_size,
                    "error_count": error_count,
                    "qber_threshold": qber_threshold,
                    "sifted_key_length": total_sifted,
                    "eps_auth": eps_auth,
            "final_key": None,
                    "final_key_length": 0,
                    "eavesdrop": eavesdrop,
                    "sample_indices": sample_indices,
                    "remaining_indices": remaining_indices,
                    "reconciliation_disclosed_bits": leak_ec,
                    "keys_match": False,
                    "reason": "privacy_amplification_mismatch",
                }

            # 3. Second PA Seed Exchange
            seed_length_2 = first_pa_output_length + min_key_length - 1
            toeplitz_seed_2_aarav = [rng.randint(0, 1) for _ in range(seed_length_2)]

            frame_a_seed2 = sim_alice.send_frame("toeplitz_seed2", {"seed": toeplitz_seed_2_aarav})
            rx_diya_seed2 = sim_bob.receive_frame(frame_a_seed2, "toeplitz_seed2")
            toeplitz_seed_2_diya = rx_diya_seed2.get("seed")

            if not isinstance(toeplitz_seed_2_diya, list) or len(toeplitz_seed_2_diya) != seed_length_2 or any(type(b) is not int or b not in (0, 1) for b in toeplitz_seed_2_diya):
                raise AuthenticationError("Invalid second Toeplitz seed format.")

            frame_b_ack2 = sim_bob.send_frame("toeplitz_seed2_ack", {"status": "ok"})
            rx_ack3_payload = sim_alice.receive_frame(frame_b_ack2, "toeplitz_seed2_ack")
            if rx_ack3_payload.get("status") != "ok":
                raise AuthenticationError("Invalid second ack status.")

            # Apply Stage 2 PA
            final_key_aarav = toeplitz_hash(pa1_out_aarav, min_key_length, toeplitz_seed_2_aarav)
            final_key_diya = toeplitz_hash(pa1_out_diya, min_key_length, toeplitz_seed_2_diya)

        else:
            # Local unauthenticated simulation mode
            pa1_out_aarav = toeplitz_hash(rec_aarav, first_pa_output_length, toeplitz_seed_1_aarav)
            pa1_out_diya = toeplitz_hash(rec_diya, first_pa_output_length, toeplitz_seed_1_aarav)

            conf_seed_len = first_pa_output_length + tag_bits - 1
            conf_seed_aarav = [rng.randint(0, 1) for _ in range(conf_seed_len)]
            conf_tag_aarav = toeplitz_hash(pa1_out_aarav, tag_bits, conf_seed_aarav)
            conf_tag_diya = toeplitz_hash(pa1_out_diya, tag_bits, conf_seed_aarav)

            if conf_tag_aarav != conf_tag_diya:
                return {
                    "secure": False,
                    "aborted": True,
                    "qber": qber,
                    "sample_size": sample_size,
                    "error_count": error_count,
                    "qber_threshold": qber_threshold,
                    "sifted_key_length": total_sifted,
                    "eps_auth": eps_auth,
            "final_key": None,
                    "final_key_length": 0,
                    "eavesdrop": eavesdrop,
                    "sample_indices": sample_indices,
                    "remaining_indices": remaining_indices,
                    "reconciliation_disclosed_bits": leak_ec,
                    "keys_match": False,
                    "reason": "privacy_amplification_mismatch",
                }

            seed_length_2 = first_pa_output_length + min_key_length - 1
            toeplitz_seed_2_aarav = [rng.randint(0, 1) for _ in range(seed_length_2)]

            final_key_aarav = toeplitz_hash(pa1_out_aarav, min_key_length, toeplitz_seed_2_aarav)
            final_key_diya = toeplitz_hash(pa1_out_diya, min_key_length, toeplitz_seed_2_aarav)

    except AuthenticationError:
        return {
            "secure": False,
            "aborted": True,
            "qber": qber,
            "sample_size": sample_size,
            "error_count": error_count,
            "qber_threshold": qber_threshold,
            "sifted_key_length": total_sifted,
            "eps_auth": eps_auth,
            "final_key": None,
            "final_key_length": 0,
            "eavesdrop": eavesdrop,
            "sample_indices": sample_indices,
            "remaining_indices": remaining_indices,
            "reconciliation_disclosed_bits": leak_ec,
            "keys_match": False,
            "reason": "authentication_failed",
        }
    except FrameExhaustionError:
        return {
            "secure": False,
            "aborted": True,
            "qber": qber,
            "sample_size": sample_size,
            "error_count": error_count,
            "qber_threshold": qber_threshold,
            "sifted_key_length": total_sifted,
            "eps_auth": eps_auth,
            "final_key": None,
            "final_key_length": 0,
            "eavesdrop": eavesdrop,
            "sample_indices": sample_indices,
            "remaining_indices": remaining_indices,
            "reconciliation_disclosed_bits": leak_ec,
            "keys_match": False,
            "reason": "frame_budget_exceeded",
        }

    return {
        "secure": True,
        "aborted": False,
        "qber": qber,
        "sample_size": sample_size,
        "error_count": error_count,
        "qber_threshold": qber_threshold,
        "sifted_key_length": total_sifted,
        "final_key": final_key_aarav,
        "final_key_length": len(final_key_aarav),
        "eavesdrop": eavesdrop,
        "sample_indices": sample_indices,
        "remaining_indices": remaining_indices,
        "reconciled_key_length": len(rec_aarav),
        "reconciliation_disclosed_bits": leak_ec,
        "reconciliation_success": rec_success,
        "keys_match": True,
        "finite_key_bound": bound_result,
        "eps_auth": eps_auth,
        "reason": "secure",
    }


if __name__ == "__main__":
    print("=== Legacy BB84 Prototype Demo ===")
    res_legacy = generate_bb84_key(key_length=8)
    print("Shared key (Aarav):", res_legacy["shared_key_aarav"])
    print("Shared key (Diya): ", res_legacy["shared_key_diya"])
    print("Keys match:        ", res_legacy["keys_match"])

    print("\n=== Secure BB84 with Tomamichel (2012) Finite-Key Bound (Clean Channel) ===")
    sec_res = run_secure_bb84(min_key_length=256, eavesdrop=False, seed=42)
    print(f"Secure: {sec_res['secure']}, Aborted: {sec_res['aborted']}, QBER: {sec_res['qber']:.4f}")
    print(f"Final Key Length: {sec_res['final_key_length']} bits")
    print(f"Reconciliation Disclosed Bits: {sec_res['reconciliation_disclosed_bits']}")
    bound = sec_res['finite_key_bound']
    print(f"Finite-Key Ell: {bound['ell']} bits (Gamma={bound['gamma']:.4f}, Delta_PA={bound['delta_pa']:.1f})")

    print("\n=== Secure BB84 Protocol (Intercept-Resend Active) ===")
    eve_res = run_secure_bb84(min_key_length=256, eavesdrop=True, seed=42)
    print(f"Secure: {eve_res['secure']}, Aborted: {eve_res['aborted']}, QBER: {eve_res['qber']:.4f}")
    print(f"Reason: {eve_res['reason']}")