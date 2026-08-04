# vol1_encoding.py

from qiskit import QuantumCircuit
from qiskit_aer import AerSimulator
import math

def encode_vote(candidates: list, chosen_candidate: str) -> dict:
    """
    Encodes a vote for one candidate out of a list of candidates
    using quantum qubits, then measures the result.
    """
    if chosen_candidate not in candidates:
        raise ValueError(f"'{chosen_candidate}' is not in the candidate list")

    num_candidates = len(candidates)
    index = candidates.index(chosen_candidate)

    num_qubits = max(1, math.ceil(math.log2(num_candidates)))
    binary_index = format(index, f'0{num_qubits}b')

    qc = QuantumCircuit(num_qubits, num_qubits)

    for i, bit in enumerate(binary_index):
        if bit == '1':
            qc.x(i)

    qc.measure(range(num_qubits), range(num_qubits))

    simulator = AerSimulator()
    job = simulator.run(qc, shots=1)
    result = job.result()
    counts = result.get_counts()

    measured_bits = list(counts.keys())[0]

    # Qiskit returns bits in reverse order (qubit 0 is rightmost) — flip it back
    measured_bits = measured_bits[::-1]

    measured_index = int(measured_bits, 2)
    decoded_candidate = candidates[measured_index]

    return {
        "chosen_candidate": chosen_candidate,
        "encoded_binary": binary_index,
        "measured_bits": measured_bits,
        "decoded_candidate": decoded_candidate,
        "num_qubits": num_qubits,
        "circuit": qc
    }


if __name__ == "__main__":
    candidates = ["Aarav", "Diya", "Rohan", "Priya"]
    for name in candidates:
        result = encode_vote(candidates, name)
        print(f"Voted for: {result['chosen_candidate']:8} -> Encoded: {result['encoded_binary']} -> Decoded back: {result['decoded_candidate']}")