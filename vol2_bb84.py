# vol2_bb84.py

import random
from qiskit import QuantumCircuit
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