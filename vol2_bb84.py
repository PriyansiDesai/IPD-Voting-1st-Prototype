# vol2_bb84.py

import random
# pyrefly: ignore[missing-import]
from qiskit import QuantumCircuit
# pyrefly: ignore[missing-import]
from qiskit_aer import AerSimulator


def generate_bb84_key(key_length: int = 8) -> dict:
    """
    Simulates the BB84 quantum key distribution protocol between
    Aarav (sender) and Diya (receiver), and returns their shared key.
    """

    simulator = AerSimulator()

    # Step 1: Aarav generates random bits and random bases
    aarav_bits = [random.randint(0, 1) for _ in range(key_length)]
    aarav_bases = [random.choice(['+', 'x']) for _ in range(key_length)]

    # Step 2: Diya randomly chooses her own bases (she doesn't know Aarav's)
    diya_bases = [random.choice(['+', 'x']) for _ in range(key_length)]

    diya_results = []

    # Step 3: For each bit, Aarav encodes it into a qubit and Diya measures it
    for i in range(key_length):
        qc = QuantumCircuit(1, 1)

        # Aarav encodes his bit using his chosen basis
        if aarav_bases[i] == '+':
            # Rectilinear basis: 0 -> |0>, 1 -> |1>
            if aarav_bits[i] == 1:
                qc.x(0)
        else:
            # Diagonal basis: 0 -> |+>, 1 -> |->
            if aarav_bits[i] == 1:
                qc.x(0)
            qc.h(0)

        # Diya measures using her own randomly chosen basis
        if diya_bases[i] == 'x':
            qc.h(0)  # undo the Hadamard if measuring in the diagonal basis

        qc.measure(0, 0)

        job = simulator.run(qc, shots=1)
        result = job.result()
        counts = result.get_counts()
        measured_bit = int(list(counts.keys())[0])

        diya_results.append(measured_bit)

    # Step 4: Aarav and Diya publicly compare BASES (not bits) and keep matches
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
        "keys_match": shared_key_aarav == shared_key_diya
    }


def get_secure_key(min_length: int = 8) -> list:
    """
    Keeps running BB84 rounds until the shared key reaches at least
    min_length bits. Returns just the final key (list of 0s and 1s),
    trimmed to exactly min_length.
    """
    final_key = []

    while len(final_key) < min_length:
        # Run BB84 with a decent batch size each round
        result = generate_bb84_key(key_length=16)
        final_key.extend(result["shared_key_aarav"])

    # Trim to exactly min_length bits
    return final_key[:min_length]


def run_secure_bb84(
    min_key_length: int = 8,
    eavesdrop: bool = False,
    sample_ratio: float = 0.5,
    qber_threshold: float = 0.11,
    seed: int | None = None,
    max_rounds: int = 10,
) -> dict:
    """
    Executes a secure BB84 Quantum Key Distribution session with:
    1. Multi-round quantum transmission with basis reconciliation (sifting).
    2. Optional genuine intercept-resend eavesdropping simulation.
    3. Random sample selection from sifted bits for QBER (Quantum Bit Error Rate) estimation.
    4. Strict exclusion/discarding of sampled test bits from the final key.
    5. Security threshold verification (aborts if QBER > qber_threshold).
    6. Safe handling of insufficient sifted bits (aborts if minimum key length cannot be met).
    7. Fully reproducible execution when seed is provided.
    """
    # ── Step 1: Input Validation ──────────────────────────────────────────────
    if min_key_length <= 0:
        return {
            "secure": False,
            "aborted": True,
            "qber": 0.0,
            "sample_size": 0,
            "error_count": 0,
            "qber_threshold": qber_threshold,
            "sifted_key_length": 0,
            "final_key": None,
            "eavesdrop": eavesdrop,
            "sample_indices": [],
            "remaining_indices": [],
            "reason": "invalid_min_key_length",
        }

    if sample_ratio < 0.0 or sample_ratio >= 1.0 or max_rounds <= 0:
        return {
            "secure": False,
            "aborted": True,
            "qber": 0.0,
            "sample_size": 0,
            "error_count": 0,
            "qber_threshold": qber_threshold,
            "sifted_key_length": 0,
            "final_key": None,
            "eavesdrop": eavesdrop,
            "sample_indices": [],
            "remaining_indices": [],
            "reason": "insufficient_sifted_bits",
        }

    rng = random.Random(seed)
    simulator = AerSimulator()

    # Determine batch size per round to gather sufficient sifted bits
    target_sifted_needed = int(min_key_length / (1.0 - sample_ratio)) + 4
    batch_size = min(max(32, 2 * target_sifted_needed), 128)

    sifted_aarav = []
    sifted_diya = []

    # ── Step 2: Transmission & Sifting Loop ───────────────────────────────────
    for round_idx in range(max_rounds):
        # Step 2a: Aarav prepares random bits and bases
        aarav_bits = [rng.randint(0, 1) for _ in range(batch_size)]
        aarav_bases = [rng.choice(['+', 'x']) for _ in range(batch_size)]

        # Step 2b: Diya selects random measurement bases
        diya_bases = [rng.choice(['+', 'x']) for _ in range(batch_size)]

        circuits = []

        if not eavesdrop:
            # Sender directly transmits qubits to Diya
            for i in range(batch_size):
                qc = QuantumCircuit(1, 1)
                # Aarav encodes bit
                if aarav_bases[i] == '+':
                    if aarav_bits[i] == 1:
                        qc.x(0)
                else:
                    if aarav_bits[i] == 1:
                        qc.x(0)
                    qc.h(0)

                # Diya measures
                if diya_bases[i] == 'x':
                    qc.h(0)
                qc.measure(0, 0)
                circuits.append(qc)

            sim_seed = rng.randint(1, 10000000) if seed is not None else None
            job = simulator.run(circuits, shots=1, seed_simulator=sim_seed)
            results = job.result().get_counts()
            if isinstance(results, dict):
                results = [results]

            diya_results = [int(list(counts.keys())[0]) for counts in results]

        else:
            # Genuine intercept-resend eavesdropping:
            # Eve intercepts, randomly chooses basis, measures, and re-prepares
            eve_bases = [rng.choice(['+', 'x']) for _ in range(batch_size)]

            for i in range(batch_size):
                qc = QuantumCircuit(1, 2)
                # Aarav encodes bit
                if aarav_bases[i] == '+':
                    if aarav_bits[i] == 1:
                        qc.x(0)
                else:
                    if aarav_bits[i] == 1:
                        qc.x(0)
                    qc.h(0)

                # Eve intercepts and measures in eve_bases[i] (stored in c[0])
                if eve_bases[i] == 'x':
                    qc.h(0)
                qc.measure(0, 0)

                # Eve re-prepares qubit based on her measurement into eve_bases[i]
                # In Qiskit, applying H right after measurement transforms collapsed
                # |0> -> |+> and |1> -> |->, restoring Eve's prepared basis state.
                if eve_bases[i] == 'x':
                    qc.h(0)

                # Diya receives Eve's qubit and measures in diya_bases[i] (stored in c[1])
                if diya_bases[i] == 'x':
                    qc.h(0)
                qc.measure(0, 1)
                circuits.append(qc)

            sim_seed = rng.randint(1, 10000000) if seed is not None else None
            job = simulator.run(circuits, shots=1, seed_simulator=sim_seed)
            results = job.result().get_counts()
            if isinstance(results, dict):
                results = [results]

            # In Qiskit bitstrings, c[1] (Diya) is at index 0, c[0] (Eve) is at index 1
            diya_results = [int(list(counts.keys())[0][0]) for counts in results]

        # Step 2c: Basis reconciliation (keep bits where Aarav and Diya used matching bases)
        for i in range(batch_size):
            if aarav_bases[i] == diya_bases[i]:
                sifted_aarav.append(aarav_bits[i])
                sifted_diya.append(diya_results[i])

        # Check if accumulated sifted bits are enough
        total_sifted = len(sifted_aarav)
        current_sample_size = int(round(total_sifted * sample_ratio))
        remaining_count = total_sifted - current_sample_size

        if remaining_count >= min_key_length:
            break

    # ── Step 3: Handle Insufficient Sifted Bits ───────────────────────────────
    total_sifted = len(sifted_aarav)
    sample_size = int(round(total_sifted * sample_ratio))
    remaining_count = total_sifted - sample_size

    if remaining_count < min_key_length or sample_size == 0:
        return {
            "secure": False,
            "aborted": True,
            "qber": 0.0,
            "sample_size": sample_size,
            "error_count": 0,
            "qber_threshold": qber_threshold,
            "sifted_key_length": total_sifted,
            "final_key": None,
            "eavesdrop": eavesdrop,
            "sample_indices": [],
            "remaining_indices": [],
            "reason": "insufficient_sifted_bits",
        }

    # ── Step 4: Sample Selection & QBER Estimation ────────────────────────────
    # Select random test bits to reveal for error estimation
    sample_indices = sorted(rng.sample(range(total_sifted), sample_size))
    sample_indices_set = set(sample_indices)

    # Compare values at sample positions
    error_count = sum(
        1 for idx in sample_indices if sifted_aarav[idx] != sifted_diya[idx]
    )
    qber = error_count / sample_size

    # All unrevealed bits form the remaining candidates
    remaining_indices = [idx for idx in range(total_sifted) if idx not in sample_indices_set]

    # ── Step 5: Security Decision & Key Extraction ────────────────────────────
    if qber > qber_threshold:
        return {
            "secure": False,
            "aborted": True,
            "qber": qber,
            "sample_size": sample_size,
            "error_count": error_count,
            "qber_threshold": qber_threshold,
            "sifted_key_length": total_sifted,
            "final_key": None,
            "eavesdrop": eavesdrop,
            "sample_indices": sample_indices,
            "remaining_indices": remaining_indices,
            "reason": "qber_threshold_exceeded",
        }

    # QBER is within threshold: unrevealed sifted bits form the final secure key
    final_key = [sifted_aarav[idx] for idx in remaining_indices]

    return {
        "secure": True,
        "aborted": False,
        "qber": qber,
        "sample_size": sample_size,
        "error_count": error_count,
        "qber_threshold": qber_threshold,
        "sifted_key_length": total_sifted,
        "final_key": final_key,
        "eavesdrop": eavesdrop,
        "sample_indices": sample_indices,
        "remaining_indices": remaining_indices,
        "reason": "secure",
    }


if __name__ == "__main__":
    result = generate_bb84_key(key_length=8)

    print("Aarav's bits:     ", result["aarav_bits"])
    print("Aarav's bases:    ", result["aarav_bases"])
    print("Diya's bases:     ", result["diya_bases"])
    print("Diya's results:   ", result["diya_results"])
    print()
    print("Shared key (Aarav):", result["shared_key_aarav"])
    print("Shared key (Diya): ", result["shared_key_diya"])
    print("Keys match:         ", result["keys_match"])

    print("\n--- Guaranteed 8-bit secure key ---")
    secure_key = get_secure_key(min_length=8)
    print("Final key:", secure_key)
    print("Length:   ", len(secure_key))

    print("\n--- Secure BB84 with Security Layer (No Eavesdropping) ---")
    sec_res = run_secure_bb84(min_key_length=8, eavesdrop=False, seed=42)
    print(f"Secure: {sec_res['secure']}, Aborted: {sec_res['aborted']}, QBER: {sec_res['qber']:.4f}, Key len: {len(sec_res['final_key']) if sec_res['final_key'] else None}")

    print("\n--- Secure BB84 with Security Layer (Eavesdropping Active) ---")
    eve_res = run_secure_bb84(min_key_length=8, eavesdrop=True, seed=42)
    print(f"Secure: {eve_res['secure']}, Aborted: {eve_res['aborted']}, QBER: {eve_res['qber']:.4f}, Key: {eve_res['final_key']}")