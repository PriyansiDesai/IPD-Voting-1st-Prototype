# test_vol1_encoding.py

from vol1_encoding import encode_vote

def run_all_tests():
    print("=== Test A: Correctness across repeated trials ===")
    candidates = ["Aarav", "Diya", "Rohan", "Priya"]
    passed = True
    for name in candidates:
        for _ in range(20):
            result = encode_vote(candidates, name)
            if result["decoded_candidate"] != name:
                passed = False
                print(f"  FAIL: voted {name}, got {result['decoded_candidate']}")
    print("  PASSED" if passed else "  FAILED")

    print("\n=== Test B: Qubit scaling ===")
    test_cases = [
        (["Aarav", "Diya"], 1),
        (["Aarav", "Diya", "Rohan"], 2),
        (["Aarav", "Diya", "Rohan", "Priya"], 2),
        (["Aarav", "Diya", "Rohan", "Priya", "Kabir"], 3),
    ]
    for cands, expected in test_cases:
        result = encode_vote(cands, cands[0])
        status = "OK" if result["num_qubits"] == expected else "FAIL"
        print(f"  {len(cands)} candidates -> {result['num_qubits']} qubits (expected {expected}) [{status}]")

    print("\n=== Test C: Invalid vote rejection ===")
    try:
        encode_vote(candidates, "Ghost")
        print("  FAILED: should have raised an error")
    except ValueError as e:
        print(f"  PASSED: correctly rejected ({e})")

    print("\n=== Test D: Larger candidate pool (10 candidates) ===")
    big_list = ["Aarav", "Diya", "Rohan", "Priya", "Kabir", "Ananya", "Vihaan", "Ishita", "Arjun", "Meera"]
    passed = all(
        encode_vote(big_list, name)["decoded_candidate"] == name
        for name in big_list
    )
    print("  PASSED" if passed else "  FAILED")


if __name__ == "__main__":
    run_all_tests()