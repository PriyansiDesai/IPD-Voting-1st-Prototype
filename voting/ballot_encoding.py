import math
from typing import List, Dict, Any
from dataclasses import dataclass

@dataclass
class M3State:
    """Internal M3 representation combining encoding metadata with the prepared quantum circuit."""
    encoding_version: str
    choice_count: int
    qubit_count: int
    encoded_bits: str
    circuit: 'qiskit.QuantumCircuit'


def encode_choice(choice_ids: List[str], selected_choice_id: str) -> Dict[str, Any]:
    """
    Encodes a selected choice into a fixed-width, zero-padded, MSB-first bit string.
    
    M3-v1 Ordering:
    Since choices have no persisted semantic order, M3-v1 ordering is defined as 
    strictly ascending (alphabetical/lexicographical) by choice ID. This makes 
    the encoding deterministic and repeatable.
    """
    if not choice_ids:
        raise ValueError("Choice list cannot be empty.")
    
    # Check for duplicates
    if len(set(choice_ids)) != len(choice_ids):
        raise ValueError("Choice list contains duplicate IDs.")
        
    if selected_choice_id not in choice_ids:
        raise ValueError("Selected choice is not in the choice list.")
        
    sorted_choices = sorted(choice_ids)
    choice_count = len(sorted_choices)
    index = sorted_choices.index(selected_choice_id)
    
    if choice_count == 1:
        qubit_count = 1
    else:
        qubit_count = math.ceil(math.log2(choice_count))
        
    encoded_bits = format(index, f'0{qubit_count}b')
    
    return {
        "encoding_version": "M3-v1",
        "choice_count": choice_count,
        "qubit_count": qubit_count,
        "encoded_bits": encoded_bits
    }

def prepare_circuit(encoded_bits: str, choice_count: int) -> 'qiskit.QuantumCircuit':
    """
    Prepares a Qiskit computational-basis circuit from the validated bit string.
    
    Qiskit ordering note:
    M3-v1 produces an MSB-first bit string (e.g., '10' means decimal 2, where MSB is '1').
    Qiskit's default ordering represents tensor products as q_{n-1} ... q_0. 
    Therefore, the character at encoded_bits[i] (where i=0 is the MSB) corresponds 
    to Qiskit qubit index (n - 1 - i).
    """
    if choice_count < 1:
        raise ValueError("Choice count must be at least 1.")
        
    if not encoded_bits:
        raise ValueError("Encoded bits cannot be empty.")
        
    if not all(c in ('0', '1') for c in encoded_bits):
        raise ValueError("Encoded bits must only contain '0' and '1'.")
        
    expected_width = 1 if choice_count == 1 else math.ceil(math.log2(choice_count))
    if len(encoded_bits) != expected_width:
        raise ValueError(f"Incorrect width: expected {expected_width} bits, got {len(encoded_bits)}.")
        
    index = int(encoded_bits, 2)
    if index < 0 or index >= choice_count:
        raise ValueError(f"Decoded index {index} is outside the valid range [0, {choice_count - 1}].")
        
    from qiskit import QuantumCircuit
    
    n = len(encoded_bits)
    qc = QuantumCircuit(n)
    
    for i, bit in enumerate(encoded_bits):
        if bit == '1':
            # Map MSB-first string index 'i' to Qiskit qubit 'n - 1 - i'
            qubit_index = n - 1 - i
            qc.x(qubit_index)
            
    return qc

def decode_choice(choice_ids: List[str], encoded_bits: str) -> str:
    """
    Decodes an M3-v1 bit string back to a choice ID.
    Validates widths, lengths, and valid ranges.
    """
    if not choice_ids:
        raise ValueError("Choice list cannot be empty.")
        
    if len(set(choice_ids)) != len(choice_ids):
        raise ValueError("Choice list contains duplicate IDs.")
        
    if not encoded_bits:
        raise ValueError("Encoded bits cannot be empty.")
        
    if not all(c in ('0', '1') for c in encoded_bits):
        raise ValueError("Encoded bits must only contain '0' and '1'.")
        
    sorted_choices = sorted(choice_ids)
    choice_count = len(sorted_choices)
    
    if choice_count == 1:
        expected_width = 1
    else:
        expected_width = math.ceil(math.log2(choice_count))
        
    if len(encoded_bits) != expected_width:
        raise ValueError(f"Incorrect width: expected {expected_width} bits, got {len(encoded_bits)}.")
        
    index = int(encoded_bits, 2)
    if index < 0 or index >= choice_count:
        raise ValueError(f"Decoded index {index} is outside the valid range [0, {choice_count - 1}].")
        
    return sorted_choices[index]
