# test_vol2_bb84.py

import unittest
from vol2_bb84 import generate_bb84_key, get_secure_key, run_secure_bb84


class TestVol2BB84Legacy(unittest.TestCase):
    """Preserves and tests the existing legacy BB84 prototype behavior."""

    def test_a_keys_match_when_bases_match(self):
        for _ in range(15):
            result = generate_bb84_key(key_length=8)
            self.assertTrue(
                result["keys_match"],
                f"Mismatch: Aarav={result['shared_key_aarav']} Diya={result['shared_key_diya']}",
            )

    def test_b_shared_key_structure(self):
        result = generate_bb84_key(key_length=8)
        expected_keys = {
            "aarav_bits",
            "aarav_bases",
            "diya_bases",
            "diya_results",
            "shared_key_aarav",
            "shared_key_diya",
            "keys_match",
        }
        self.assertTrue(expected_keys.issubset(result.keys()))
        self.assertEqual(len(result["aarav_bits"]), 8)
        self.assertEqual(len(result["diya_results"]), 8)

    def test_c_basis_match_rate_roughly_half(self):
        total_bits = 0
        total_matches = 0
        for _ in range(25):
            result = generate_bb84_key(key_length=16)
            total_bits += 16
            total_matches += len(result["shared_key_aarav"])
        match_rate = total_matches / total_bits
        self.assertGreater(match_rate, 0.35)
        self.assertLess(match_rate, 0.65)

    def test_d_different_key_lengths(self):
        for length in [4, 8, 16]:
            result = generate_bb84_key(key_length=length)
            self.assertEqual(len(result["aarav_bits"]), length)
            self.assertEqual(len(result["diya_results"]), length)
            self.assertTrue(result["keys_match"])

    def test_e_guaranteed_minimum_key_length(self):
        for min_len in [4, 8, 16]:
            key = get_secure_key(min_length=min_len)
            self.assertEqual(len(key), min_len)


class TestVol2BB84Security(unittest.TestCase):
    """M4: Unit test suite for secure BB84 QKD protocol and security layer."""

    def test_secure_no_eavesdrop_success(self):
        """A normal session without eavesdropping has 0 QBER and returns a valid key."""
        res = run_secure_bb84(min_key_length=8, eavesdrop=False, seed=42)

        self.assertTrue(res["secure"])
        self.assertFalse(res["aborted"])
        self.assertEqual(res["qber"], 0.0)
        self.assertEqual(res["error_count"], 0)
        self.assertFalse(res["eavesdrop"])
        self.assertEqual(res["reason"], "secure")
        self.assertIsNotNone(res["final_key"])
        self.assertGreaterEqual(len(res["final_key"]), 8)

    def test_eavesdrop_intercept_resend_aborts(self):
        """Eavesdropping via intercept-resend produces detectable QBER (~25%) and aborts."""
        # Test with multiple random seeds
        aborted_count = 0
        seeds = [42, 101, 202, 303, 404]
        for s in seeds:
            res = run_secure_bb84(
                min_key_length=8,
                eavesdrop=True,
                sample_ratio=0.5,
                qber_threshold=0.11,
                seed=s,
            )
            self.assertTrue(res["eavesdrop"])
            self.assertGreater(res["qber"], 0.0)
            if res["qber"] > res["qber_threshold"]:
                self.assertTrue(res["aborted"])
                self.assertFalse(res["secure"])
                self.assertIsNone(res["final_key"])
                self.assertEqual(res["reason"], "qber_threshold_exceeded")
                aborted_count += 1

        # With intercept-resend producing ~25% error rate, all or nearly all should abort
        self.assertGreaterEqual(aborted_count, len(seeds) - 1)

    def test_sampled_test_bits_excluded_from_final_key(self):
        """Sampled/test bits revealed for QBER estimation MUST NOT be in the final key."""
        res = run_secure_bb84(min_key_length=12, eavesdrop=False, sample_ratio=0.5, seed=777)
        self.assertTrue(res["secure"])

        sample_set = set(res["sample_indices"])
        remaining_set = set(res["remaining_indices"])

        # 1. Sampled positions and remaining key positions must be strictly disjoint
        self.assertTrue(
            sample_set.isdisjoint(remaining_set),
            "Sample indices and remaining indices must have zero overlap",
        )

        # 2. Together they must partition the sifted key
        self.assertEqual(len(sample_set) + len(remaining_set), res["sifted_key_length"])

        # 3. Final key length must match remaining indices
        self.assertEqual(len(res["final_key"]), len(remaining_set))

    def test_final_key_minimum_length_when_secure(self):
        """Final key must meet or exceed the requested min_key_length."""
        for target_len in [4, 8, 16, 24]:
            res = run_secure_bb84(min_key_length=target_len, eavesdrop=False, seed=target_len * 10)
            self.assertTrue(res["secure"])
            self.assertIsNotNone(res["final_key"])
            self.assertGreaterEqual(len(res["final_key"]), target_len)

    def test_insufficient_key_condition_handled_safely(self):
        """Insufficient sifted bits are safely reported and never return an undersized key."""
        # Case A: sample_ratio = 1.0 leaves 0 bits for final key
        res_ratio = run_secure_bb84(min_key_length=8, sample_ratio=1.0)
        self.assertTrue(res_ratio["aborted"])
        self.assertFalse(res_ratio["secure"])
        self.assertIsNone(res_ratio["final_key"])
        self.assertEqual(res_ratio["reason"], "insufficient_sifted_bits")

        # Case B: invalid min_key_length <= 0
        res_zero = run_secure_bb84(min_key_length=0)
        self.assertTrue(res_zero["aborted"])
        self.assertFalse(res_zero["secure"])
        self.assertIsNone(res_zero["final_key"])

        # Case C: max_rounds is too small for an enormous requested key length
        res_exhaust = run_secure_bb84(min_key_length=1000, max_rounds=1)
        self.assertTrue(res_exhaust["aborted"])
        self.assertFalse(res_exhaust["secure"])
        self.assertIsNone(res_exhaust["final_key"])
        self.assertEqual(res_exhaust["reason"], "insufficient_sifted_bits")

    def test_seeded_execution_reproducibility(self):
        """Runs with identical seeds must produce identical QBER, indices, and keys."""
        run1 = run_secure_bb84(min_key_length=8, eavesdrop=False, seed=12345)
        run2 = run_secure_bb84(min_key_length=8, eavesdrop=False, seed=12345)

        self.assertEqual(run1["qber"], run2["qber"])
        self.assertEqual(run1["error_count"], run2["error_count"])
        self.assertEqual(run1["sample_indices"], run2["sample_indices"])
        self.assertEqual(run1["remaining_indices"], run2["remaining_indices"])
        self.assertEqual(run1["final_key"], run2["final_key"])
        self.assertEqual(run1["sifted_key_length"], run2["sifted_key_length"])

        # Reproducibility also holds under eavesdropping
        eve1 = run_secure_bb84(min_key_length=8, eavesdrop=True, seed=54321)
        eve2 = run_secure_bb84(min_key_length=8, eavesdrop=True, seed=54321)

        self.assertEqual(eve1["qber"], eve2["qber"])
        self.assertEqual(eve1["sample_indices"], eve2["sample_indices"])
        self.assertEqual(eve1["aborted"], eve2["aborted"])


def run_all_tests():
    """CLI test runner preserving legacy output format while executing test suites."""
    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite()
    suite.addTest(loader.loadTestsFromTestCase(TestVol2BB84Legacy))
    suite.addTest(loader.loadTestsFromTestCase(TestVol2BB84Security))
    runner = unittest.TextTestRunner(verbosity=2)
    return runner.run(suite)


if __name__ == "__main__":
    unittest.main()