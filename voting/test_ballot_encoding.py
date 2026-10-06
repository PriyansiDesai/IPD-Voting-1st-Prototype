# pyrefly: ignore [missing-import]
import pytest
from ballot_encoding import encode_choice, prepare_circuit, decode_choice

def test_encode_ordering_and_repeatability():
    # Choices given in random order
    choices = ["Charlie", "Alice", "Bob"]
    # Sorted order should be: Alice (0), Bob (1), Charlie (2)
    result = encode_choice(choices, "Bob")
    # choice_count=3 -> 2 qubits. Bob is index 1 -> '01'
    assert result["encoding_version"] == "M3-v1"
    assert result["choice_count"] == 3
    assert result["qubit_count"] == 2
    assert result["encoded_bits"] == "01"

    # Check it's repeatable and doesn't change if input list order changes
    choices_reversed = ["Bob", "Alice", "Charlie"]
    result2 = encode_choice(choices_reversed, "Bob")
    assert result == result2

def test_qubit_count_boundaries():
    # 1 choice -> 1 qubit, state 0
    res1 = encode_choice(["OnlyChoice"], "OnlyChoice")
    assert res1["qubit_count"] == 1
    assert res1["encoded_bits"] == "0"

    # 2 choices -> 1 qubit
    res2 = encode_choice(["A", "B"], "B")
    assert res2["qubit_count"] == 1
    assert res2["encoded_bits"] == "1"

    # 3 choices -> 2 qubits
    res3 = encode_choice(["A", "B", "C"], "C")
    assert res3["qubit_count"] == 2
    assert res3["encoded_bits"] == "10" # index 2

    # 4 choices -> 2 qubits
    res4 = encode_choice(["A", "B", "C", "D"], "D")
    assert res4["qubit_count"] == 2
    assert res4["encoded_bits"] == "11" # index 3

    # 5 choices -> 3 qubits
    res5 = encode_choice(["A", "B", "C", "D", "E"], "E")
    assert res5["qubit_count"] == 3
    assert res5["encoded_bits"] == "100" # index 4

    # 15 choices -> 4 qubits
    choices_15 = [str(i).zfill(2) for i in range(15)]
    res15 = encode_choice(choices_15, "14")
    assert res15["qubit_count"] == 4
    assert res15["encoded_bits"] == "1110" # index 14

    # 16 choices -> 4 qubits
    choices_16 = [str(i).zfill(2) for i in range(16)]
    res16 = encode_choice(choices_16, "15")
    assert res16["qubit_count"] == 4
    assert res16["encoded_bits"] == "1111" # index 15

    # 17 choices -> 5 qubits
    choices_17 = [str(i).zfill(2) for i in range(17)]
    res17 = encode_choice(choices_17, "16")
    assert res17["qubit_count"] == 5
    assert res17["encoded_bits"] == "10000" # index 16

def test_first_and_last_choices():
    choices = ["Option1", "Option2", "Option3", "Option4", "Option5"] # 5 choices -> 3 qubits
    # First choice
    res_first = encode_choice(choices, "Option1")
    assert res_first["encoded_bits"] == "000" # zero-padded

    # Last choice
    res_last = encode_choice(choices, "Option5")
    assert res_last["encoded_bits"] == "100"

def test_invalid_inputs():
    with pytest.raises(ValueError, match="empty"):
        encode_choice([], "A")

    with pytest.raises(ValueError, match="duplicate"):
        encode_choice(["A", "B", "A"], "B")

    with pytest.raises(ValueError, match="not in the choice list"):
        encode_choice(["A", "B"], "C")

def test_prepare_circuit():
    # pyrefly: ignore [missing-import]
    from qiskit.quantum_info import Statevector
    # MSB-first string '10' -> index 0 is '1', index 1 is '0'.
    # For choice_count=3 or 4, width is 2.
    qc = prepare_circuit("10", choice_count=3)
    assert qc.num_qubits == 2

    # Verify no measurements
    ops = [instr.operation.name for instr in qc.data]
    assert 'measure' not in ops

    # Verify the prepared state matches the input string
    # Qiskit Statevector prints states as |q1 q0> which should match "10"
    state = Statevector.from_instruction(qc)
    # The state should be exactly |10>
    probabilities = state.probabilities_dict()
    assert len(probabilities) == 1
    assert "10" in probabilities
    assert probabilities["10"] == 1.0

def test_prepare_circuit_invalid():
    with pytest.raises(ValueError, match="at least 1"):
        prepare_circuit("0", choice_count=0)
    with pytest.raises(ValueError, match="empty"):
        prepare_circuit("", choice_count=1)
    with pytest.raises(ValueError, match="must only contain '0' and '1'"):
        prepare_circuit("102", choice_count=5)
    with pytest.raises(ValueError, match="Incorrect width"):
        prepare_circuit("000", choice_count=3) # width 3 instead of 2
    with pytest.raises(ValueError, match="outside the valid range"):
        prepare_circuit("11", choice_count=3) # index 3, but valid is 0-2

def test_decode_choice():
    choices = ["C", "A", "B"] # Sorted: A, B, C
    assert decode_choice(choices, "00") == "A"
    assert decode_choice(choices, "01") == "B"
    assert decode_choice(choices, "10") == "C"

def test_decode_invalid():
    choices = ["A", "B", "C"] # 3 choices -> 2 qubits expected width

    with pytest.raises(ValueError, match="Incorrect width"):
        decode_choice(choices, "000") # width 3 instead of 2

    with pytest.raises(ValueError, match="outside the valid range"):
        decode_choice(choices, "11") # index 3, but valid is 0-2

    with pytest.raises(ValueError, match="duplicate"):
        decode_choice(["A", "A"], "0")

def test_no_voter_information():
    # Confirm result dictionary has exactly the keys we want and no extra voter info
    result = encode_choice(["A", "B"], "A")
    expected_keys = {"encoding_version", "choice_count", "qubit_count", "encoded_bits"}
    assert set(result.keys()) == expected_keys

def test_no_voter_identity_or_ballot_values_logged(caplog):
    import logging
    caplog.set_level(logging.DEBUG)

    choices = ["Alice", "Bob", "Charlie"]
    selected = "Bob"
    result = encode_choice(choices, selected)

    # ensure "Bob", "01" (the encoded bits), or "1" (the index) are not logged
    log_text = caplog.text
    assert selected not in log_text
    assert result["encoded_bits"] not in log_text
    assert "1" not in log_text # Index of Bob is 1

def test_prepare_circuit_invalid_1_state_one_choice():
    with pytest.raises(ValueError) as excinfo:
        prepare_circuit("1", choice_count=1)
    assert "outside the valid range" in str(excinfo.value)

def test_decode_choice_invalid_1_state_one_choice():
    with pytest.raises(ValueError) as excinfo:
        decode_choice(["OnlyChoice"], "1")
    assert "outside the valid range" in str(excinfo.value)
