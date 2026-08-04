# test_vol2_bb84.py

from vol2_bb84 import generate_bb84_key, get_secure_key

def run_all_tests():
    print("=== Test A: Keys always match when bases match ===")
    passed = True
    for _ in range(30):
        result = generate_bb84_key(key_length=8)
        if not result["keys_match"]:
            passed = False
            print(f"  FAIL: Aarav={result['shared_key_aarav']} Diya={result['shared_key_diya']}")
    print("  PASSED" if passed else "  FAILED")

    print("\n=== Test B: Shared key is never empty across many runs ===")
    empty_count = 0
    for _ in range(30):
        result = generate_bb84_key(key_length=8)
        if len(result["shared_key_aarav"]) == 0:
            empty_count += 1
    if empty_count > 0:
        print(f"  WARNING: {empty_count}/30 runs produced an empty key (rare but possible with short key_length)")
    else:
        print("  PASSED: no empty keys in 30 runs")

    print("\n=== Test C: Basis match rate is roughly 50% ===")
    total_bits = 0
    total_matches = 0
    for _ in range(50):
        result = generate_bb84_key(key_length=16)
        total_bits += 16
        total_matches += len(result["shared_key_aarav"])
    match_rate = total_matches / total_bits
    print(f"  Match rate: {match_rate:.2%} (expected roughly 50%)")
    status = "OK" if 0.35 < match_rate < 0.65 else "FAIL"
    print(f"  [{status}]")

    print("\n=== Test D: Different key lengths work ===")
    for length in [4, 8, 16, 32]:
        result = generate_bb84_key(key_length=length)
        ok = (
            len(result["aarav_bits"]) == length
            and len(result["diya_results"]) == length
            and result["keys_match"]
        )
        print(f"  key_length={length}: {'OK' if ok else 'FAIL'}")

    print("\n=== Test E: Guaranteed minimum key length ===")
    for min_len in [4, 8, 16]:
        key = get_secure_key(min_length=min_len)
        status = "OK" if len(key) == min_len else "FAIL"
        print(f"  min_length={min_len}: got {len(key)} bits [{status}]")


if __name__ == "__main__":
    run_all_tests()