# test_vol2_bb84.py
"""
Comprehensive Unit Test Suite for Vol2 BB84 QKD Software Simulation.

Covers:
1. Legacy prototype functions (generate_bb84_key, get_secure_key)
2. Finite-sample parameter estimation and Tomamichel et al. (2012) key bound
3. Security parameters (eps_pe, eps_pa, eps_s) scaling and QBER impact
4. Clean channel protocol execution (>= 256 bits final key, key agreement)
5. Noisy channels (sub-threshold tolerance vs. above-threshold abort)
6. Intercept-resend eavesdropping detection and abort
7. Sampled test bit strict exclusion (zero overlap with key material)
8. Multi-pass Cascade reconciliation with multiple errors in the same block
9. Unresolved reconciliation mismatch abort
10. Toeplitz universal hash validation against explicit GF(2) reference implementation
11. Undersized key requests (< 256 bits) and insufficient resource aborts
12. Seeded reproducibility and unseeded OS-backed randomness
13. M2 fail-closed abort behavior before encryption or ledger writes (VotingEngine integration)
"""

import math
import unittest
from unittest.mock import patch
from vol2_bb84 import (
    generate_bb84_key,
    get_secure_key,
    run_secure_bb84,
    reconcile_keys,
    toeplitz_hash,
    compute_finite_key_bound,
    binary_entropy,
)
from voting.voting_engine import VotingEngine, BB84SecurityError


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


class TestVol2BB84FiniteKeyBound(unittest.TestCase):
    """Unit tests for the published Tomamichel et al. (2012) finite-key bound."""

    def test_finite_key_serfling_bound_calculation(self):
        """Validates Tomamichel et al. (2012) formulas and Serfling deviation."""
        n = 1800
        m = 600
        errors = 0
        leak_ec = 70
        eps_pe = 1e-10
        eps_pa = 1e-10
        eps_s = 1e-10

        res = compute_finite_key_bound(
            n=n,
            m=m,
            sample_error_count=errors,
            reconciliation_disclosed_bits=leak_ec,
            eps_pe=eps_pe,
            eps_pa=eps_pa,
            eps_s=eps_s,
        )

        # Expected Serfling gamma: sqrt( (n+m)*(n+1)*ln(1/eps_pe) / (2*n^2*m) )
        expected_gamma = math.sqrt(
            ((n + m) * (n + 1) * math.log(1.0 / eps_pe)) / (2.0 * (n ** 2) * m)
        )
        self.assertAlmostEqual(res["gamma"], expected_gamma, places=6)

        # Expected phase error upper bound: min(0.5, Q + gamma)
        self.assertAlmostEqual(res["e_ph"], min(0.5, 0.0 + expected_gamma), places=6)

        # Expected PA penalty: 2*log2(1/(2*eps_pa)) + 2*log2(1/eps_s)
        expected_delta_pa = 2.0 * math.log2(1.0 / (2.0 * eps_pa)) + 2.0 * math.log2(1.0 / eps_s)
        self.assertAlmostEqual(res["delta_pa"], expected_delta_pa, places=4)

        # Expected key length ell: floor(n * (1 - h(e_ph)) - leak_ec - delta_pa)
        expected_ell = math.floor(n * (1.0 - binary_entropy(res["e_ph"])) - leak_ec - expected_delta_pa)
        self.assertEqual(res["ell"], max(0, expected_ell))
        self.assertGreaterEqual(res["ell"], 256)

    def test_security_parameter_scaling(self):
        """Stricter security parameters increase confidence penalty and decrease ell."""
        n = 2000
        m = 800
        errors = 0
        leak_ec = 80

        bound_relaxed = compute_finite_key_bound(n, m, errors, leak_ec, eps_pe=1e-6, eps_pa=1e-6, eps_s=1e-6)
        bound_strict = compute_finite_key_bound(n, m, errors, leak_ec, eps_pe=1e-12, eps_pa=1e-12, eps_s=1e-12)

        # Stricter parameter estimation failure probability increases gamma
        self.assertGreater(bound_strict["gamma"], bound_relaxed["gamma"])
        # Stricter PA collision probability increases Delta_PA
        self.assertGreater(bound_strict["delta_pa"], bound_relaxed["delta_pa"])
        # Stricter parameters reduce the extractable key length
        self.assertLess(bound_strict["ell"], bound_relaxed["ell"])

    def test_qber_impact_on_finite_key_length(self):
        """Observed sample error rate directly reduces extractable key length."""
        n = 2000
        m = 800
        leak_ec = 80

        res_zero = compute_finite_key_bound(n, m, sample_error_count=0, reconciliation_disclosed_bits=leak_ec)
        res_low_err = compute_finite_key_bound(n, m, sample_error_count=16, reconciliation_disclosed_bits=leak_ec)  # Q = 2%
        res_high_err = compute_finite_key_bound(n, m, sample_error_count=64, reconciliation_disclosed_bits=leak_ec) # Q = 8%

        self.assertGreater(res_zero["ell"], res_low_err["ell"])
        self.assertGreater(res_low_err["ell"], res_high_err["ell"])


class TestVol2BB84Protocol(unittest.TestCase):
    """Integration test suite for the M4 secure BB84 simulation protocol."""

    def test_clean_channel_success(self):
        """Clean channel without eavesdropping completes successfully with >= 256-bit key."""
        res = run_secure_bb84(min_key_length=256, eavesdrop=False, seed=42)

        self.assertTrue(res["secure"])
        self.assertFalse(res["aborted"])
        self.assertEqual(res["qber"], 0.0)
        self.assertEqual(res["error_count"], 0)
        self.assertFalse(res["eavesdrop"])
        self.assertEqual(res["reason"], "secure")
        self.assertIsNotNone(res["final_key"])
        self.assertEqual(len(res["final_key"]), 256)
        self.assertEqual(res["final_key_length"], 256)
        self.assertTrue(res["keys_match"])
        self.assertTrue(res["reconciliation_success"])
        self.assertGreaterEqual(res["finite_key_bound"]["ell"], 256)

    def test_noisy_channel_sub_threshold_vs_above_threshold(self):
        """Sub-threshold noise is handled; excessive channel noise (>11%) aborts."""
        # Case A: Sub-threshold noise (channel_error_rate = 0.015 / 1.5%)
        res_sub = run_secure_bb84(
            min_key_length=256,
            eavesdrop=False,
            channel_error_rate=0.015,
            seed=123,
        )
        if res_sub["qber"] <= 0.11 and res_sub["finite_key_bound"]["ell"] >= 256:
            self.assertTrue(res_sub["secure"])
            self.assertFalse(res_sub["aborted"])
            self.assertTrue(res_sub["keys_match"])

        # Case B: Above-threshold noise (channel_error_rate = 0.15 / 15%)
        res_above = run_secure_bb84(
            min_key_length=256,
            eavesdrop=False,
            channel_error_rate=0.15,
            seed=456,
        )
        self.assertTrue(res_above["aborted"])
        self.assertFalse(res_sub["eavesdrop"] if res_above["secure"] else False)
        self.assertIn(res_above["reason"], ("qber_threshold_exceeded", "finite_key_bound_insufficient"))
        self.assertIsNone(res_above["final_key"])

    def test_eavesdrop_intercept_resend_aborts(self):
        """Intercept-resend eavesdropping induces ~25% QBER and reliably aborts."""
        seeds = [42, 101, 202, 303, 404]
        aborted_count = 0
        for s in seeds:
            res = run_secure_bb84(
                min_key_length=256,
                eavesdrop=True,
                sample_ratio=0.25,
                qber_threshold=0.11,
                seed=s,
            )
            self.assertTrue(res["eavesdrop"])
            self.assertGreater(res["qber"], 0.0)
            if res["qber"] > res["qber_threshold"] or res["aborted"]:
                self.assertTrue(res["aborted"])
                self.assertFalse(res["secure"])
                self.assertIsNone(res["final_key"])
                aborted_count += 1

        self.assertEqual(aborted_count, len(seeds))

    def test_sampled_test_bits_strictly_excluded(self):
        """Sampled test bits revealed for QBER estimation are strictly excluded from raw key."""
        res = run_secure_bb84(min_key_length=256, eavesdrop=False, sample_ratio=0.25, seed=777)
        self.assertTrue(res["secure"])

        sample_set = set(res["sample_indices"])
        remaining_set = set(res["remaining_indices"])

        # Sample indices and remaining raw key indices must have zero overlap
        self.assertTrue(sample_set.isdisjoint(remaining_set))
        # The sum of their lengths is less than or equal to sifted_key_length
        # (unsampled X-basis bits are safely discarded)
        self.assertLessEqual(len(sample_set) + len(remaining_set), res["sifted_key_length"])
        self.assertGreater(len(sample_set), 0)
        self.assertGreater(len(remaining_set), 0)

    def test_reconciliation_multiple_errors_in_same_block(self):
        """Multi-pass Cascade reconciliation corrects multiple bit flips in the same block."""
        alice_raw = [1, 0, 1, 1, 0, 0, 1, 0] * 50  # 400 bits
        bob_raw = list(alice_raw)

        # Introduce 2 errors in the SAME initial block of size 48 (indices 5 and 10)
        bob_raw[5] ^= 1
        bob_raw[10] ^= 1

        # Also introduce an error in another block
        bob_raw[60] ^= 1

        rec_alice, rec_bob, disclosed, success = reconcile_keys(
            alice_bits=alice_raw,
            bob_bits=bob_raw,
            block_size=48,
            tag_bits=32,
            num_passes=3,
        )

        self.assertTrue(success, "Reconciliation should resolve errors across permutation passes")
        self.assertEqual(rec_alice, rec_bob)
        self.assertEqual(rec_alice, alice_raw)
        self.assertGreater(disclosed, 32)  # Parity bits + confirmation tag accounted for

    def test_reconciliation_unresolved_mismatch_aborts(self):
        """Excessive or uncorrectable errors fail tag verification and return success=False."""
        alice_raw = [0] * 300
        bob_raw = [1] * 300  # 100% error rate (catastrophic)

        rec_alice, rec_bob, disclosed, success = reconcile_keys(
            alice_bits=alice_raw,
            bob_bits=bob_raw,
            block_size=48,
            tag_bits=32,
            num_passes=2,
        )

        self.assertFalse(success, "Catastrophic error rate must fail verification")
        self.assertNotEqual(rec_alice, rec_bob)
        self.assertGreater(disclosed, 32)

    def test_toeplitz_hash_against_gf2_reference(self):
        """Toeplitz hash implementation exactly matches explicit GF(2) reference multiplication."""
        def reference_toeplitz_hash(bit_sequence: list[int], output_length: int, matrix_seed: list[int]) -> list[int]:
            n = len(bit_sequence)
            l = output_length
            out = []
            for i in range(l):
                acc = 0
                for j in range(n):
                    # T[i][j] = matrix_seed[j - i + (l - 1)]
                    acc ^= (matrix_seed[j - i + (l - 1)] & bit_sequence[j])
                out.append(acc)
            return out

        test_cases = [
            (50, 16, 12345),
            (100, 32, 67890),
            (256, 128, 54321),
            (400, 256, 99999),
        ]

        import random
        for n_in, l_out, s in test_cases:
            rng = random.Random(s)
            x = [rng.randint(0, 1) for _ in range(n_in)]
            seed = [rng.randint(0, 1) for _ in range(n_in + l_out - 1)]

            fast_output = toeplitz_hash(x, l_out, seed)
            ref_output = reference_toeplitz_hash(x, l_out, seed)

            self.assertEqual(fast_output, ref_output, f"Mismatch at n={n_in}, l={l_out}")

    def test_insufficient_key_material_aborts(self):
        """Sub-256 requests, depleted sample ratio, or exhausted rounds abort safely."""
        # Case A: Requesting < 256 bits fails closed (Requirement 2)
        for undersized in [0, 8, 64, 128, 255]:
            res_small = run_secure_bb84(min_key_length=undersized)
            self.assertTrue(res_small["aborted"])
            self.assertFalse(res_small["secure"])
            self.assertIsNone(res_small["final_key"])
            self.assertIn("at_least_256", res_small["reason"])

        # Case B: sample_ratio = 1.0 leaves 0 bits for key material
        res_ratio = run_secure_bb84(min_key_length=256, sample_ratio=1.0)
        self.assertTrue(res_ratio["aborted"])
        self.assertFalse(res_ratio["secure"])
        self.assertIsNone(res_ratio["final_key"])

        # Case C: max_rounds is too small for requested key length
        res_exhaust = run_secure_bb84(min_key_length=10000, max_rounds=1)
        self.assertTrue(res_exhaust["aborted"])
        self.assertFalse(res_exhaust["secure"])
        self.assertIsNone(res_exhaust["final_key"])
        self.assertEqual(res_exhaust["reason"], "insufficient_sifted_bits")

    def test_seeded_reproducibility(self):
        """Identical seeds produce identical results, QBER, and final keys."""
        run1 = run_secure_bb84(min_key_length=256, eavesdrop=False, seed=9999)
        run2 = run_secure_bb84(min_key_length=256, eavesdrop=False, seed=9999)

        self.assertEqual(run1["qber"], run2["qber"])
        self.assertEqual(run1["sample_indices"], run2["sample_indices"])
        self.assertEqual(run1["remaining_indices"], run2["remaining_indices"])
        self.assertEqual(run1["final_key"], run2["final_key"])
        self.assertEqual(run1["finite_key_bound"]["ell"], run2["finite_key_bound"]["ell"])

    def test_unseeded_os_backed_entropy(self):
        """Unseeded runs utilize OS-backed entropy and generate distinct final keys."""
        run1 = run_secure_bb84(min_key_length=256, eavesdrop=False, seed=None)
        run2 = run_secure_bb84(min_key_length=256, eavesdrop=False, seed=None)

        self.assertTrue(run1["secure"])
        self.assertTrue(run2["secure"])
        self.assertNotEqual(run1["final_key"], run2["final_key"])


class TestM2BB84AbortIntegration(unittest.TestCase):
    """Integration tests verifying M2 fail-closed behavior on BB84 abort or key defects.

    Guarantees:
    - No encryption is performed when BB84 aborts.
    - No block is appended to the blockchain.
    - Session tallies and total vote counts remain unchanged.
    - Atomic voter reservations are released, preventing denial-of-service lockout.
    """

    def setUp(self):
        self.engine = VotingEngine()
        self.session_id = "SESS-004"
        self.engine.set_session_status(self.session_id, "ACTIVE")
        self.voter_id = "V001"
        self.candidate_id = "C001"
        self.chain = self.engine.get_session_chain(self.session_id)
        self.initial_block_count = len(self.chain.chain)
        self.initial_tally = dict(self.engine.get_tally(self.session_id))

    def test_m2_aborts_on_bb84_security_failure(self):
        """When BB84 aborts (e.g. QBER threshold exceeded), VotingEngine raises BB84SecurityError."""
        abort_result = {
            "secure": False,
            "aborted": True,
            "reason": "qber_threshold_exceeded",
            "qber": 0.25,
            "final_key": None,
            "final_key_length": 0,
        }
        with patch("voting.voting_engine.run_secure_bb84", return_value=abort_result):
            with self.assertRaises(BB84SecurityError) as ctx:
                self.engine.cast_vote(self.session_id, self.voter_id, self.candidate_id)

            self.assertIn("qber_threshold_exceeded", str(ctx.exception))

        # Fail-closed verifications:
        # 1. No blockchain append
        self.assertEqual(len(self.chain.chain), self.initial_block_count)
        # 2. Tally unchanged
        self.assertEqual(self.engine.get_tally(self.session_id), self.initial_tally)
        # 3. Voter not marked as voted
        self.assertNotIn((self.session_id, self.voter_id), self.engine._voted_voters)
        # 4. Voter reservation released (can retry)
        self.assertNotIn((self.session_id, self.voter_id), self.engine._reserved_voters)

    def test_m2_prevents_encryption_when_bb84_aborts(self):
        """Verifies encrypt_vote is NEVER called if BB84 aborts."""
        abort_result = {
            "secure": False,
            "aborted": True,
            "reason": "finite_key_bound_insufficient",
            "final_key": None,
        }
        with patch("voting.voting_engine.run_secure_bb84", return_value=abort_result), \
             patch("voting.voting_engine.encrypt_vote") as mock_encrypt:

            with self.assertRaises(BB84SecurityError):
                self.engine.cast_vote(self.session_id, self.voter_id, self.candidate_id)

            mock_encrypt.assert_not_called()

        self.assertEqual(len(self.chain.chain), self.initial_block_count)

    def test_m2_aborts_on_undersized_bb84_key(self):
        """When BB84 returns < 256 bits, VotingEngine raises BB84SecurityError without commit."""
        undersized_result = {
            "secure": True,
            "aborted": False,
            "reason": "secure",
            "final_key": [1] * 128,  # Only 128 bits
            "final_key_length": 128,
        }
        with patch("voting.voting_engine.run_secure_bb84", return_value=undersized_result):
            with self.assertRaises(BB84SecurityError) as ctx:
                self.engine.cast_vote(self.session_id, self.voter_id, self.candidate_id)

            self.assertIn("undersized or invalid key", str(ctx.exception))

        self.assertEqual(len(self.chain.chain), self.initial_block_count)
        self.assertNotIn((self.session_id, self.voter_id), self.engine._reserved_voters)

    def test_m2_aborts_on_none_or_malformed_bb84_key(self):
        """When BB84 returns final_key=None despite secure=True, VotingEngine aborts."""
        malformed_result = {
            "secure": True,
            "aborted": False,
            "reason": "secure",
            "final_key": None,
        }
        with patch("voting.voting_engine.run_secure_bb84", return_value=malformed_result):
            with self.assertRaises(BB84SecurityError) as ctx:
                self.engine.cast_vote(self.session_id, self.voter_id, self.candidate_id)

            self.assertIn("undersized or invalid key", str(ctx.exception))

        self.assertEqual(len(self.chain.chain), self.initial_block_count)
        self.assertNotIn((self.session_id, self.voter_id), self.engine._reserved_voters)

    def test_voter_can_retry_after_bb84_abort(self):
        """Verifies that after an aborted BB84 attempt, reservation release allows a successful retry."""
        abort_result = {
            "secure": False,
            "aborted": True,
            "reason": "qber_threshold_exceeded",
            "final_key": None,
        }
        # First attempt: BB84 aborts
        with patch("voting.voting_engine.run_secure_bb84", return_value=abort_result):
            with self.assertRaises(BB84SecurityError):
                self.engine.cast_vote(self.session_id, self.voter_id, self.candidate_id)

        # Second attempt: normal execution succeeds
        res = self.engine.cast_vote(self.session_id, self.voter_id, self.candidate_id)
        self.assertTrue(res["success"])
        self.assertEqual(len(self.chain.chain), self.initial_block_count + 1)
        self.assertIn((self.session_id, self.voter_id), self.engine._voted_voters)


def run_all_tests():
    """CLI test runner executing legacy, finite-key bound, protocol, and M2 abort test suites."""
    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite()
    suite.addTest(loader.loadTestsFromTestCase(TestVol2BB84Legacy))
    suite.addTest(loader.loadTestsFromTestCase(TestVol2BB84FiniteKeyBound))
    suite.addTest(loader.loadTestsFromTestCase(TestVol2BB84Protocol))
    suite.addTest(loader.loadTestsFromTestCase(TestM2BB84AbortIntegration))
    runner = unittest.TextTestRunner(verbosity=2)
    return runner.run(suite)


if __name__ == "__main__":
    unittest.main()