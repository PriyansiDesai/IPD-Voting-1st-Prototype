import math
from typing import TYPE_CHECKING, Any, Dict, List
from dataclasses import dataclass

if TYPE_CHECKING:
    # pyrefly: ignore [missing-import]
    from qiskit import QuantumCircuit


@dataclass
class M3State:
    """Internal M3 representation combining encoding metadata with the prepared quantum circuit."""
    encoding_version: str
    choice_count: int
    qubit_count: int
    encoded_bits: str
    circuit: "QuantumCircuit"


def encode_choice(choice_ids: List[str], selected_choice_id: str) -> Dict[str, Any]:
    """
    Encodes a selected choice into a fixed-width, zero-padded, MSB-first bit string.

    M3-v1 ordering is ascending by choice ID, making the encoding deterministic.
    """
    if not choice_ids:
        raise ValueError("Choice list cannot be empty.")

    if len(set(choice_ids)) != len(choice_ids):
        raise ValueError("Choice list contains duplicate IDs.")

    if selected_choice_id not in choice_ids:
        raise ValueError("Selected choice is not in the choice list.")

    sorted_choices = sorted(choice_ids)
    choice_count = len(sorted_choices)
    index = sorted_choices.index(selected_choice_id)

    qubit_count = 1 if choice_count == 1 else math.ceil(math.log2(choice_count))
    encoded_bits = format(index, f"0{qubit_count}b")

    return {
        "encoding_version": "M3-v1",
        "choice_count": choice_count,
        "qubit_count": qubit_count,
        "encoded_bits": encoded_bits,
    }


def prepare_circuit(encoded_bits: str, choice_count: int) -> "QuantumCircuit":
    """
    Prepares a Qiskit computational-basis circuit from the validated bit string.

    M3-v1 strings are MSB-first. The first character maps to Qiskit qubit
    index n - 1 because Qiskit displays tensor products as q_(n-1) ... q_0.
    """
    if choice_count < 1:
        raise ValueError("Choice count must be at least 1.")

    if not encoded_bits:
        raise ValueError("Encoded bits cannot be empty.")

    if not all(c in ("0", "1") for c in encoded_bits):
        raise ValueError("Encoded bits must only contain '0' and '1'.")

    expected_width = 1 if choice_count == 1 else math.ceil(math.log2(choice_count))
    if len(encoded_bits) != expected_width:
        raise ValueError(
            f"Incorrect width: expected {expected_width} bits, got {len(encoded_bits)}."
        )

    index = int(encoded_bits, 2)
    if index >= choice_count:
        raise ValueError(
            f"Decoded index {index} is outside the valid range [0, {choice_count - 1}]."
        )

    # Keep Qiskit as a runtime dependency only when circuit preparation is requested.
    # pyrefly: ignore [missing-import]
    from qiskit import QuantumCircuit

    n = len(encoded_bits)
    qc = QuantumCircuit(n)

    for i, bit in enumerate(encoded_bits):
        if bit == "1":
            qubit_index = n - 1 - i
            qc.x(qubit_index)

    return qc


def decode_choice(choice_ids: List[str], encoded_bits: str) -> str:
    """Decodes an M3-v1 bit string back to a choice ID."""
    if not choice_ids:
        raise ValueError("Choice list cannot be empty.")

    if len(set(choice_ids)) != len(choice_ids):
        raise ValueError("Choice list contains duplicate IDs.")

    if not encoded_bits:
        raise ValueError("Encoded bits cannot be empty.")

    if not all(c in ("0", "1") for c in encoded_bits):
        raise ValueError("Encoded bits must only contain '0' and '1'.")

    sorted_choices = sorted(choice_ids)
    choice_count = len(sorted_choices)

    expected_width = 1 if choice_count == 1 else math.ceil(math.log2(choice_count))
    if len(encoded_bits) != expected_width:
        raise ValueError(
            f"Incorrect width: expected {expected_width} bits, got {len(encoded_bits)}."
        )

    index = int(encoded_bits, 2)
    if index >= choice_count:
        raise ValueError(
            f"Decoded index {index} is outside the valid range [0, {choice_count - 1}]."
        )

    return sorted_choices[index]