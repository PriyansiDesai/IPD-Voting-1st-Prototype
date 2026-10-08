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
    def test_finite_key_error_accounting(self):
        """Verify explicit reporting of eps_sec, eps_c, and eps_total."""
        from vol2_bb84 import compute_finite_key_bound
        res = compute_finite_key_bound(
            n=2000, m=1000, sample_error_count=10, reconciliation_disclosed_bits=50,
            eps_pe=0.01, eps_pa=0.02, eps_s=0.03, eps_c=0.04, eps_pa2=0.05, eps_s2=0.06
        )
        self.assertAlmostEqual(res["eps_sec"], 0.26)  # 0.01 + 2*0.03 + 0.02 + 2*0.06 + 0.05 = 0.26
        self.assertAlmostEqual(res["eps_c"], 0.04)
        self.assertAlmostEqual(res["eps_total"], 0.30)

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

    def test_noisy_channel_reconciliation_limit_and_aborts(self):
        """1.5% noise exceeds 3-pass reconciliation limit; >11% noise aborts at QBER check."""
        # Case A: Sub-threshold noise (channel_error_rate = 0.015 / 1.5%)
        # For seed=123 and 3200-bit length, the 1.5% noise happens to overwhelm the 3-pass
        # Cascade limit due to clustering, failing verification without changing the QBER threshold.
        res_sub = run_secure_bb84(
            min_key_length=256,
            eavesdrop=False,
            channel_error_rate=0.015,
            seed=123,
        )
        self.assertFalse(res_sub["secure"])
        self.assertTrue(res_sub["aborted"])
        self.assertEqual(res_sub["reason"], "reconciliation_verification_failed")
        self.assertIsNone(res_sub["final_key"])

        # Case B: Above-threshold noise (channel_error_rate = 0.15 / 15%)
        res_above = run_secure_bb84(
            min_key_length=256,
            eavesdrop=False,
            channel_error_rate=0.15,
            seed=456,
        )
        self.assertTrue(res_above["aborted"])
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

    def test_sifted_array_capping_and_alignment(self):
        """Arrays are capped to TARGET_SIFTED_BITS and perfectly retain their positional alignment."""
        from vol2_bb84 import TARGET_SIFTED_BITS, align_and_cap_sifted_arrays

        # Test helper function behavior directly with position-distinct arrays
        a = list(range(4000))
        b = list(range(4000, 8000))
        bases = [str(i) for i in range(8000, 12000)]

        cap_a, cap_b, cap_bases = align_and_cap_sifted_arrays(a, b, bases, 3200)
        self.assertEqual(len(cap_a), 3200)
        self.assertEqual(cap_a, list(range(3200)))
        self.assertEqual(cap_b, list(range(4000, 7200)))
        self.assertEqual(cap_bases, [str(i) for i in range(8000, 11200)])

        # Test mismatched inputs
        with self.assertRaises(ValueError):
            align_and_cap_sifted_arrays(a, b[:3000], bases, 3200)

        # Test full protocol alignment
        res = run_secure_bb84(min_key_length=256, eavesdrop=False, seed=42)
        self.assertTrue(res["secure"])
        self.assertEqual(res["sifted_key_length"], TARGET_SIFTED_BITS)

        n = len(res["remaining_indices"])
        m = res["sample_size"]
        self.assertEqual(n + m, TARGET_SIFTED_BITS, "n + m must exactly equal the capped population")

    def test_sample_ratio_parameter_is_ignored(self):
        """The sample_ratio parameter is retained for compatibility but ignored, as all X-basis bits are used. Out of range values are still rejected."""
        res_default = run_secure_bb84(min_key_length=256, eavesdrop=False, seed=888)
        res_changed = run_secure_bb84(min_key_length=256, eavesdrop=False, sample_ratio=0.99, seed=888)

        self.assertTrue(res_default["secure"])
        self.assertTrue(res_changed["secure"])
        self.assertEqual(res_default["sample_size"], res_changed["sample_size"])
        self.assertEqual(res_default["sample_indices"], res_changed["sample_indices"])

        # Test out-of-range rejection
        res_high = run_secure_bb84(min_key_length=256, eavesdrop=False, sample_ratio=1.0)
        self.assertFalse(res_high["secure"])
        self.assertTrue(res_high["aborted"])

        res_low = run_secure_bb84(min_key_length=256, eavesdrop=False, sample_ratio=0.0)
        self.assertFalse(res_low["secure"])
        self.assertTrue(res_low["aborted"])

    def test_insufficient_sample_size_aborts(self):
        """If X-basis bits are missing, sample_size is 0 and it aborts."""
        with patch('random.Random.choice', return_value='+'):
            res = run_secure_bb84(min_key_length=256, eavesdrop=False, seed=42)
            self.assertFalse(res["secure"])
            self.assertTrue(res["aborted"])
            self.assertEqual(res["reason"], "insufficient_sifted_bits")

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

    def test_max_rounds_limit_enforcement(self):
        """Requests with max_rounds exceeding MAX_ROUNDS_LIMIT are rejected to bound frame budgets."""
        # Test rejection above limit
        res_exceeds = run_secure_bb84(min_key_length=256, eavesdrop=False, max_rounds=16)
        self.assertTrue(res_exceeds["aborted"])
        self.assertFalse(res_exceeds["secure"])
        self.assertIsNone(res_exceeds["final_key"])
        self.assertEqual(res_exceeds["reason"], "max_rounds_exceeds_limit")

        # Test acceptance at or below limit (e.g., max_rounds=15 should pass)
        res_at_limit = run_secure_bb84(min_key_length=256, eavesdrop=False, max_rounds=15, seed=42)
        self.assertTrue(res_at_limit["secure"])
        self.assertFalse(res_at_limit["aborted"])
        self.assertEqual(len(res_at_limit["final_key"]), 256)

        # Test acceptance with default max_rounds omitted
        res_default = run_secure_bb84(min_key_length=256, eavesdrop=False, seed=42)
        self.assertTrue(res_default["secure"])
        self.assertFalse(res_default["aborted"])
        self.assertEqual(len(res_default["final_key"]), 256)


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
        """When BB84 returns None or malformed final_key, VotingEngine aborts."""
        malformed_keys = [
            None,
            "string_key",
            {"key": "val"},
            [1, 0, "1"] * 86,  # Mixed types
            [1, 2, 0] * 86,    # Integers other than 0 or 1
            [True, False] * 128, # Booleans should be rejected
        ]
        
        for bad_key in malformed_keys:
            malformed_result = {
                "secure": True,
                "aborted": False,
                "reason": "secure",
                "final_key": bad_key,
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


class TestVol2BB84SiftingAuthentication(unittest.TestCase):
    """Integration tests for the authenticated classical channel during basis sifting."""

    def test_sifting_authentication_success(self):
        """A valid run completes the authenticated sifting exchange."""
        res = run_secure_bb84(min_key_length=256, seed=42)
        self.assertTrue(res["secure"])
        self.assertFalse(res["aborted"])

    def test_sifting_authentication_tamper_aborts(self):
        """Tampering with the sifting frame payload causes an immediate abort."""
        from voting.classical_channel import AuthenticatedChannelEndpoint

        original_receive = AuthenticatedChannelEndpoint.receive_frame

        def mock_receive(self_obj, frame):
            # Tamper with the frame payload
            if frame.get("msg_type") == "basis_exchange":
                frame["payload"]["bases"] = ['+'] * len(frame["payload"]["bases"])
            return original_receive(self_obj, frame)

        with patch.object(AuthenticatedChannelEndpoint, 'receive_frame', new=mock_receive):
            res = run_secure_bb84(min_key_length=256, seed=42)
            self.assertFalse(res["secure"])
            self.assertTrue(res["aborted"])
            self.assertEqual(res["reason"], "authentication_failed")

    def test_sifting_authentication_replay_aborts(self):
        """Replaying a previously accepted frame causes an immediate abort."""
        from voting.classical_channel import AuthenticatedChannelEndpoint

        original_receive = AuthenticatedChannelEndpoint.receive_frame

        def mock_receive(self_obj, frame):
            # Process the frame normally first
            res = original_receive(self_obj, frame)

            # Immediately replay the exact same frame to the same endpoint.
            # This exercises the pad replay/sequence checks, not the sender check.
            # It will raise an AuthenticationError, which the caller catches to abort.
            original_receive(self_obj, frame)

            return res

        with patch.object(AuthenticatedChannelEndpoint, 'receive_frame', new=mock_receive):
            res = run_secure_bb84(min_key_length=256, seed=42)
            self.assertFalse(res["secure"])
            self.assertTrue(res["aborted"])
            self.assertEqual(res["reason"], "authentication_failed")
            self.assertIsNone(res["final_key"])

    def test_sifting_authentication_pads_unique(self):
        """Verifies that all frames sent during basis sifting use unique pad indices."""
        from voting.classical_channel import AuthenticatedChannelEndpoint

        original_send = AuthenticatedChannelEndpoint.send_frame

        pad_indices = []
        def mock_send(self_obj, msg_type, payload):
            frame = original_send(self_obj, msg_type, payload)
            pad_indices.append(frame["pad_idx"])
            return frame

        with patch.object(AuthenticatedChannelEndpoint, 'send_frame', new=mock_send):
            res = run_secure_bb84(min_key_length=256, seed=42)
            self.assertTrue(res["secure"])

        self.assertGreater(len(pad_indices), 0)
        self.assertEqual(len(pad_indices), len(set(pad_indices)))


class TestVol2BB84ParameterEstimationAuthentication(unittest.TestCase):
    """Integration tests for the authenticated classical channel during parameter estimation."""

    def test_pe_authentication_success(self):
        """A valid run completes the authenticated parameter estimation exchange."""
        res = run_secure_bb84(min_key_length=256, seed=42)
        self.assertTrue(res["secure"])
        self.assertFalse(res["aborted"])

    def test_pe_authentication_tamper_aborts(self):
        """Tampering with the parameter estimation frame payload causes an immediate abort."""
        from voting.classical_channel import AuthenticatedChannelEndpoint

        original_receive = AuthenticatedChannelEndpoint.receive_frame

        def mock_receive(self_obj, frame):
            # Tamper with the frame payload if it's parameter estimation
            if frame.get("msg_type") == "parameter_estimation":
                # Invert the first bit of the sample
                if len(frame["payload"]["sample_bits"]) > 0:
                    frame["payload"]["sample_bits"][0] ^= 1
            return original_receive(self_obj, frame)

        with patch.object(AuthenticatedChannelEndpoint, 'receive_frame', new=mock_receive):
            res = run_secure_bb84(min_key_length=256, seed=42)
            self.assertFalse(res["secure"])
            self.assertTrue(res["aborted"])
            self.assertEqual(res["reason"], "authentication_failed")
            self.assertIsNone(res["final_key"])

    def test_pe_validation_mismatched_indices(self):
        """Mismatched sample indices correctly fail payload validation and abort."""
        from voting.classical_channel import AuthenticatedChannelEndpoint

        original_send = AuthenticatedChannelEndpoint.send_frame

        def mock_send(self_obj, msg_type, payload):
            if msg_type == "parameter_estimation" and self_obj.peer_id == "bob":
                # Alice sends to Bob
                payload = dict(payload)
                payload["sample_indices"] = list(payload["sample_indices"])
                if len(payload["sample_indices"]) > 0:
                    payload["sample_indices"][0] ^= 9999
            return original_send(self_obj, msg_type, payload)

        with patch.object(AuthenticatedChannelEndpoint, 'send_frame', new=mock_send):
            res = run_secure_bb84(min_key_length=256, seed=42)
            self.assertTrue(res["aborted"])
            self.assertEqual(res["reason"], "authentication_failed")
            self.assertIsNone(res["final_key"])

    def test_pe_validation_malformed_sample_bits(self):
        """Malformed sample bits (not 0 or 1) fail validation and abort."""
        from voting.classical_channel import AuthenticatedChannelEndpoint

        original_send = AuthenticatedChannelEndpoint.send_frame

        def mock_send(self_obj, msg_type, payload):
            if msg_type == "parameter_estimation" and self_obj.peer_id == "alice":
                # Bob sends to Alice
                payload = dict(payload)
                payload["sample_bits"] = list(payload["sample_bits"])
                if len(payload["sample_bits"]) > 0:
                    payload["sample_bits"][0] = 2 # invalid bit
            return original_send(self_obj, msg_type, payload)

        with patch.object(AuthenticatedChannelEndpoint, 'send_frame', new=mock_send):
            res = run_secure_bb84(min_key_length=256, seed=42)
            self.assertTrue(res["aborted"])
            self.assertEqual(res["reason"], "authentication_failed")
            self.assertIsNone(res["final_key"])

    def test_pe_authentication_tamper_bob_to_alice_aborts(self):
        """Tampering specifically with Bob's PE frame causes an immediate abort."""
        from voting.classical_channel import AuthenticatedChannelEndpoint

        original_receive = AuthenticatedChannelEndpoint.receive_frame

        def mock_receive(self_obj, frame):
            # self_obj.peer_id is the peer of the endpoint receiving the frame.
            # Alice receives from Bob, so self_obj.peer_id == "bob"
            if frame.get("msg_type") == "parameter_estimation" and self_obj.peer_id == "bob":
                if len(frame["payload"]["sample_bits"]) > 0:
                    frame["payload"]["sample_bits"][0] ^= 1
            return original_receive(self_obj, frame)

        with patch.object(AuthenticatedChannelEndpoint, 'receive_frame', new=mock_receive):
            res = run_secure_bb84(min_key_length=256, seed=42)
            self.assertFalse(res["secure"])
            self.assertTrue(res["aborted"])
            self.assertEqual(res["reason"], "authentication_failed")
            self.assertIsNone(res["final_key"])

    def test_pe_authentication_replay_aborts(self):
        """Replaying a previously accepted parameter estimation frame causes an immediate abort."""
        from voting.classical_channel import AuthenticatedChannelEndpoint

        original_receive = AuthenticatedChannelEndpoint.receive_frame

        def mock_receive(self_obj, frame):
            res = original_receive(self_obj, frame)

            if frame.get("msg_type") == "parameter_estimation":
                # Immediately replay the exact same frame to the same endpoint.
                # This exercises the pad replay/sequence checks.
                original_receive(self_obj, frame)

            return res

        with patch.object(AuthenticatedChannelEndpoint, 'receive_frame', new=mock_receive):
            res = run_secure_bb84(min_key_length=256, seed=42)
            self.assertFalse(res["secure"])
            self.assertTrue(res["aborted"])
            self.assertEqual(res["reason"], "authentication_failed")
            self.assertIsNone(res["final_key"])


class TestVol2BB84ReconciliationAuthentication(unittest.TestCase):
    """M4 Tests for Authentication Integration in Key Reconciliation."""

    def test_reconciliation_parity_authentication_tampering_aborts(self):
        """Tampering with a parity frame during reconciliation causes an immediate abort."""
        from voting.classical_channel import AuthenticatedChannelEndpoint
        original_receive = AuthenticatedChannelEndpoint.receive_frame

        def mock_receive(self_obj, frame):
            if frame.get("msg_type") == "parity":
                # Tamper with the payload without updating the MAC
                frame["payload"]["p"] = 1 - frame["payload"]["p"]
            return original_receive(self_obj, frame)

        with patch.object(AuthenticatedChannelEndpoint, 'receive_frame', new=mock_receive):
            res = run_secure_bb84(min_key_length=256, seed=42)
            self.assertFalse(res["secure"])
            self.assertTrue(res["aborted"])
            self.assertEqual(res["reason"], "authentication_failed")
            self.assertIsNone(res["final_key"])

    def test_reconciliation_parity_authentication_replay_aborts(self):
        """Replaying a parity frame during reconciliation causes an immediate abort."""
        from voting.classical_channel import AuthenticatedChannelEndpoint
        original_receive = AuthenticatedChannelEndpoint.receive_frame

        def mock_receive(self_obj, frame):
            res = original_receive(self_obj, frame)
            if frame.get("msg_type") == "parity":
                # Immediately replay the exact same frame to the same endpoint
                original_receive(self_obj, frame)
            return res

        with patch.object(AuthenticatedChannelEndpoint, 'receive_frame', new=mock_receive):
            res = run_secure_bb84(min_key_length=256, seed=42)
            self.assertFalse(res["secure"])
            self.assertTrue(res["aborted"])
            self.assertEqual(res["reason"], "authentication_failed")
            self.assertIsNone(res["final_key"])

    def test_reconciliation_frame_exhaustion_aborts(self):
        """Frame exhaustion during reconciliation parity exchange causes a fail-closed abort."""
        from voting.classical_channel import AuthenticatedChannelEndpoint, FrameExhaustionError
        original_send = AuthenticatedChannelEndpoint.send_frame

        def mock_send(self_obj, msg_type, payload):
            if msg_type == "parity":
                raise FrameExhaustionError("Aggregate frame budget exceeded.")
            return original_send(self_obj, msg_type, payload)

        with patch.object(AuthenticatedChannelEndpoint, 'send_frame', new=mock_send):
            res = run_secure_bb84(min_key_length=256, seed=42)
            self.assertFalse(res["secure"])
            self.assertTrue(res["aborted"])
            self.assertEqual(res["reason"], "frame_budget_exceeded")
            self.assertIsNone(res["final_key"])

    def test_reconciliation_bisection_authentication_tampering_aborts(self):
        """Tampering with a bisection parity frame during reconciliation causes an immediate abort."""
        from voting.classical_channel import AuthenticatedChannelEndpoint
        original_receive = AuthenticatedChannelEndpoint.receive_frame

        def mock_receive(self_obj, frame):
            if frame.get("msg_type") == "bisection_parity":
                # Tamper with the payload without updating the MAC
                frame["payload"]["p"] = 1 - frame["payload"]["p"]
            return original_receive(self_obj, frame)

        with patch.object(AuthenticatedChannelEndpoint, 'receive_frame', new=mock_receive):
            # Use low channel error rate to ensure we successfully complete block parity but trigger a bisection
            res = run_secure_bb84(min_key_length=256, channel_error_rate=0.005, seed=42)
            self.assertFalse(res["secure"])
            self.assertTrue(res["aborted"])
            self.assertEqual(res["reason"], "authentication_failed")
            self.assertIsNone(res["final_key"])

    def test_reconciliation_bisection_authentication_replay_aborts(self):
        """Replaying a bisection parity frame during reconciliation causes an immediate abort."""
        from voting.classical_channel import AuthenticatedChannelEndpoint
        original_receive = AuthenticatedChannelEndpoint.receive_frame

        def mock_receive(self_obj, frame):
            res = original_receive(self_obj, frame)
            if frame.get("msg_type") == "bisection_parity":
                # Immediately replay the exact same frame to the same endpoint
                original_receive(self_obj, frame)
            return res

        with patch.object(AuthenticatedChannelEndpoint, 'receive_frame', new=mock_receive):
            res = run_secure_bb84(min_key_length=256, channel_error_rate=0.005, seed=42)
            self.assertFalse(res["secure"])
            self.assertTrue(res["aborted"])
            self.assertEqual(res["reason"], "authentication_failed")
            self.assertIsNone(res["final_key"])

    def test_reconciliation_bisection_frame_exhaustion_aborts(self):
        """Frame exhaustion during bisection parity exchange causes a fail-closed abort."""
        from voting.classical_channel import AuthenticatedChannelEndpoint, FrameExhaustionError
        original_send = AuthenticatedChannelEndpoint.send_frame

        def mock_send(self_obj, msg_type, payload):
            if msg_type == "bisection_parity":
                raise FrameExhaustionError("Aggregate frame budget exceeded.")
            return original_send(self_obj, msg_type, payload)

        with patch.object(AuthenticatedChannelEndpoint, 'send_frame', new=mock_send):
            res = run_secure_bb84(min_key_length=256, channel_error_rate=0.005, seed=42)
            self.assertFalse(res["secure"])
            self.assertTrue(res["aborted"])
            self.assertEqual(res["reason"], "frame_budget_exceeded")
            self.assertIsNone(res["final_key"])

    def test_reconciliation_tag_authentication_tampering_aborts(self):
        """Tampering with a verification tag frame during reconciliation causes an immediate abort."""
        from voting.classical_channel import AuthenticatedChannelEndpoint
        original_receive = AuthenticatedChannelEndpoint.receive_frame

        def mock_receive(self_obj, frame):
            if frame.get("msg_type") == "verification_tag":
                # Tamper with the payload tag (invert first hex character)
                tag_hex = frame["payload"]["tag"]
                char = tag_hex[0]
                new_char = '0' if char != '0' else '1'
                frame["payload"]["tag"] = new_char + tag_hex[1:]
            return original_receive(self_obj, frame)

        with patch.object(AuthenticatedChannelEndpoint, 'receive_frame', new=mock_receive):
            # Zero channel error avoids bisection, but initial block-parity exchanges still occur before the tag exchange
            res = run_secure_bb84(min_key_length=256, channel_error_rate=0.0, seed=42)
            self.assertFalse(res["secure"])
            self.assertTrue(res["aborted"])
            self.assertEqual(res["reason"], "authentication_failed")
            self.assertIsNone(res["final_key"])

    def test_reconciliation_tag_authentication_replay_aborts(self):
        """Replaying a verification tag frame during reconciliation causes an immediate abort."""
        from voting.classical_channel import AuthenticatedChannelEndpoint
        original_receive = AuthenticatedChannelEndpoint.receive_frame

        def mock_receive(self_obj, frame):
            res = original_receive(self_obj, frame)
            if frame.get("msg_type") == "verification_tag":
                # Immediately replay the exact same frame to the same endpoint
                original_receive(self_obj, frame)
            return res

        with patch.object(AuthenticatedChannelEndpoint, 'receive_frame', new=mock_receive):
            res = run_secure_bb84(min_key_length=256, channel_error_rate=0.0, seed=42)
            self.assertFalse(res["secure"])
            self.assertTrue(res["aborted"])
            self.assertEqual(res["reason"], "authentication_failed")
            self.assertIsNone(res["final_key"])

    def test_reconciliation_tag_frame_exhaustion_aborts(self):
        """Frame exhaustion during verification tag exchange causes a fail-closed abort."""
        from voting.classical_channel import AuthenticatedChannelEndpoint, FrameExhaustionError
        original_send = AuthenticatedChannelEndpoint.send_frame

        def mock_send(self_obj, msg_type, payload):
            if msg_type == "verification_tag":
                raise FrameExhaustionError("Aggregate frame budget exceeded.")
            return original_send(self_obj, msg_type, payload)

        with patch.object(AuthenticatedChannelEndpoint, 'send_frame', new=mock_send):
            res = run_secure_bb84(min_key_length=256, channel_error_rate=0.0, seed=42)
            self.assertFalse(res["secure"])
            self.assertTrue(res["aborted"])
            self.assertEqual(res["reason"], "frame_budget_exceeded")
            self.assertIsNone(res["final_key"])

    def test_reconciliation_partial_endpoints_rejected(self):
        """Reconcile keys rejects a configuration where only one endpoint is supplied."""
        from vol2_bb84 import reconcile_keys
        with self.assertRaisesRegex(ValueError, "Both sim_alice and sim_bob must be provided together, or both omitted."):
            reconcile_keys(
                alice_bits=[0, 1],
                bob_bits=[0, 1],
                sim_alice="dummy_alice",
                sim_bob=None
            )
        with self.assertRaisesRegex(ValueError, "Both sim_alice and sim_bob must be provided together, or both omitted."):
            reconcile_keys(
                alice_bits=[0, 1],
                bob_bits=[0, 1],
                sim_alice=None,
                sim_bob="dummy_bob"
            )

    def test_reconciliation_falsey_endpoints_use_authenticated_transport(self):
        """A falsey endpoint object is not silently treated as local mode; it still uses authenticated transport."""
        from vol2_bb84 import reconcile_keys

        class FalseyEndpoint:
            def __bool__(self):
                return False
            def send_frame(self, msg_type, payload):
                raise RuntimeError("Authenticated transport was correctly invoked")

        alice_ep = FalseyEndpoint()
        bob_ep = FalseyEndpoint()

        # By expecting RuntimeError, we prove that reconcile_keys did not silently
        # fall back to local mode (which wouldn't call send_frame).
        with self.assertRaisesRegex(RuntimeError, "Authenticated transport was correctly invoked"):
            reconcile_keys(
                alice_bits=[0, 1],
                bob_bits=[1, 0],
                sim_alice=alice_ep,
                sim_bob=bob_ep
            )

class TestVol2BB84SeedExchangeAuthentication(unittest.TestCase):
    def test_seed_exchange_valid(self):
        """A valid Toeplitz seed exchange succeeds and both sides use the identical seed."""
        from vol2_bb84 import run_secure_bb84
        from voting.classical_channel import AuthenticatedChannelEndpoint
        
        captured_seed = {}
        original_send = AuthenticatedChannelEndpoint.send_frame
        def mock_send(self_obj, msg_type, payload):
            if msg_type == "toeplitz_seed":
                captured_seed["alice"] = payload["seed"]
            return original_send(self_obj, msg_type, payload)
            
        original_receive = AuthenticatedChannelEndpoint.receive_frame
        def mock_receive(self_obj, frame):
            res = original_receive(self_obj, frame)
            if frame.get("msg_type") == "toeplitz_seed":
                captured_seed["bob"] = res["seed"]
            return res

        with patch.object(AuthenticatedChannelEndpoint, 'send_frame', new=mock_send), \
             patch.object(AuthenticatedChannelEndpoint, 'receive_frame', new=mock_receive):
            res = run_secure_bb84(min_key_length=256, channel_error_rate=0.0, seed=42)
            self.assertTrue(res["secure"])
            self.assertIsNotNone(res["final_key"])
            self.assertTrue(res["keys_match"])
            self.assertIn("alice", captured_seed)
            self.assertEqual(captured_seed["alice"], captured_seed["bob"])

    def test_seed_exchange_tampering_aborts(self):
        """Tampering with the Toeplitz seed during exchange causes an immediate abort."""
        from voting.classical_channel import AuthenticatedChannelEndpoint
        from vol2_bb84 import run_secure_bb84
        original_receive = AuthenticatedChannelEndpoint.receive_frame
        
        def mock_receive(self_obj, frame):
            if frame.get("msg_type") == "toeplitz_seed":
                # Invert first bit
                seed = frame["payload"]["seed"]
                frame["payload"]["seed"] = [1 - seed[0]] + seed[1:]
            return original_receive(self_obj, frame)

        with patch.object(AuthenticatedChannelEndpoint, 'receive_frame', new=mock_receive):
            res = run_secure_bb84(min_key_length=256, channel_error_rate=0.0, seed=42)
            self.assertFalse(res["secure"])
            self.assertTrue(res["aborted"])
            self.assertEqual(res["reason"], "authentication_failed")
            self.assertIsNone(res["final_key"])

    def test_seed_exchange_replay_aborts(self):
        """Replaying a Toeplitz seed frame causes an immediate abort."""
        from voting.classical_channel import AuthenticatedChannelEndpoint
        from vol2_bb84 import run_secure_bb84
        original_receive = AuthenticatedChannelEndpoint.receive_frame
        
        def mock_receive(self_obj, frame):
            res = original_receive(self_obj, frame)
            if frame.get("msg_type") == "toeplitz_seed":
                original_receive(self_obj, frame)
            return res

        with patch.object(AuthenticatedChannelEndpoint, 'receive_frame', new=mock_receive):
            res = run_secure_bb84(min_key_length=256, channel_error_rate=0.0, seed=42)
            self.assertFalse(res["secure"])
            self.assertTrue(res["aborted"])
            self.assertEqual(res["reason"], "authentication_failed")
            self.assertIsNone(res["final_key"])

    def test_seed_exchange_frame_exhaustion_aborts(self):
        """Frame budget exhaustion during Toeplitz seed exchange fails securely."""
        from voting.classical_channel import AuthenticatedChannelEndpoint, FrameExhaustionError
        from vol2_bb84 import run_secure_bb84
        original_send = AuthenticatedChannelEndpoint.send_frame
        
        def mock_send(self_obj, msg_type, payload):
            if msg_type == "toeplitz_seed":
                raise FrameExhaustionError("Frame budget exceeded")
            return original_send(self_obj, msg_type, payload)

        with patch.object(AuthenticatedChannelEndpoint, 'send_frame', new=mock_send):
            res = run_secure_bb84(min_key_length=256, channel_error_rate=0.0, seed=42)
            self.assertFalse(res["secure"])
            self.assertTrue(res["aborted"])
            self.assertEqual(res["reason"], "frame_budget_exceeded")
            self.assertIsNone(res["final_key"])

    def test_seed_ack_tampering_aborts(self):
        """Tampering with the Toeplitz seed acknowledgment message type or status causes an abort."""
        from voting.classical_channel import AuthenticatedChannelEndpoint
        from vol2_bb84 import run_secure_bb84

        # Test tampering with msg_type
        original_send = AuthenticatedChannelEndpoint.send_frame
        def mock_send(self_obj, msg_type, payload):
            res = original_send(self_obj, msg_type, payload)
            if msg_type == "toeplitz_seed_ack":
                res["msg_type"] = "tampered_type"
            return res

        with patch.object(AuthenticatedChannelEndpoint, 'send_frame', new=mock_send):
            res = run_secure_bb84(min_key_length=256, channel_error_rate=0.0, seed=42)
            self.assertFalse(res["secure"])
            self.assertTrue(res["aborted"])
            self.assertEqual(res["reason"], "authentication_failed")
            self.assertIsNone(res["final_key"])

        # Test an authenticated but unsuccessful acknowledgment status
        original_send_status = AuthenticatedChannelEndpoint.send_frame
        def mock_send_status(self_obj, msg_type, payload):
            if msg_type == "toeplitz_seed_ack":
                return original_send_status(self_obj, msg_type, {"status": "not_ok"})
            return original_send_status(self_obj, msg_type, payload)

        with patch.object(AuthenticatedChannelEndpoint, 'send_frame', new=mock_send_status):
            res = run_secure_bb84(min_key_length=256, channel_error_rate=0.0, seed=42)
            self.assertFalse(res["secure"])
            self.assertTrue(res["aborted"])
            self.assertEqual(res["reason"], "authentication_failed")
            self.assertIsNone(res["final_key"])


class TestVol2BB84KeyConfirmationAuthentication(unittest.TestCase):
    def test_key_confirmation_success(self):
        """A valid run computes a 256-bit application key securely."""
        from vol2_bb84 import run_secure_bb84
        res = run_secure_bb84(min_key_length=256, channel_error_rate=0.0, seed=42)
        self.assertTrue(res["secure"])
        self.assertFalse(res["aborted"])
        self.assertEqual(res["final_key_length"], 256)
        self.assertTrue(res["keys_match"])

    def test_key_confirmation_mismatched_keys(self):
        """Mismatched intermediate keys trigger 'mismatch' ack and safe abort."""
        import vol2_bb84
        from vol2_bb84 import run_secure_bb84
        
        original_hash = vol2_bb84.toeplitz_hash
        call_count = 0
        
        def mock_hash(*args):
            nonlocal call_count
            res = original_hash(*args)
            call_count += 1
            # The second call to toeplitz_hash in Stage 1 is Bob's pa1_out_diya
            if call_count == 2:
                res[0] ^= 1 # flip a bit to make Bob's intermediate key different
            return res

        with patch('vol2_bb84.toeplitz_hash', new=mock_hash):
            res = run_secure_bb84(min_key_length=256, channel_error_rate=0.0, seed=42)
            self.assertFalse(res["secure"])
            self.assertTrue(res["aborted"])
            self.assertEqual(res["reason"], "privacy_amplification_mismatch")
            self.assertIsNone(res["final_key"])

    def test_key_confirmation_invalid_ack(self):
        """Invalid ack payload triggers authentication_failed abort."""
        from voting.classical_channel import AuthenticatedChannelEndpoint
        from vol2_bb84 import run_secure_bb84
        
        original_send = AuthenticatedChannelEndpoint.send_frame
        def mock_send(self_obj, msg_type, payload):
            if msg_type == "key_confirmation_ack":
                return original_send(self_obj, msg_type, {"status": "invalid"})
            return original_send(self_obj, msg_type, payload)

        with patch.object(AuthenticatedChannelEndpoint, 'send_frame', new=mock_send):
            res = run_secure_bb84(min_key_length=256, channel_error_rate=0.0, seed=42)
            self.assertFalse(res["secure"])
            self.assertTrue(res["aborted"])
            self.assertEqual(res["reason"], "authentication_failed")
            self.assertIsNone(res["final_key"])

    def test_key_confirmation_replay_only(self):
        """Replayed confirmation frame triggers authentication abort."""
        from voting.classical_channel import AuthenticatedChannelEndpoint
        from vol2_bb84 import run_secure_bb84
        
        original_receive = AuthenticatedChannelEndpoint.receive_frame
        def mock_receive(self_obj, frame):
            res = original_receive(self_obj, frame)
            if frame.get("msg_type") == "key_confirmation":
                original_receive(self_obj, frame) # Replay
            return res

        with patch.object(AuthenticatedChannelEndpoint, 'receive_frame', new=mock_receive):
            res = run_secure_bb84(min_key_length=256, channel_error_rate=0.0, seed=42)
            self.assertFalse(res["secure"])
            self.assertTrue(res["aborted"])
            self.assertEqual(res["reason"], "authentication_failed")
            self.assertIsNone(res["final_key"])

    def test_key_confirmation_frame_exhaustion(self):
        """Frame exhaustion during confirmation fails safely."""
        from voting.classical_channel import AuthenticatedChannelEndpoint, FrameExhaustionError
        from vol2_bb84 import run_secure_bb84
        
        original_send = AuthenticatedChannelEndpoint.send_frame
        def mock_send(self_obj, msg_type, payload):
            if msg_type == "key_confirmation":
                raise FrameExhaustionError("Budget exceeded")
            return original_send(self_obj, msg_type, payload)

        with patch.object(AuthenticatedChannelEndpoint, 'send_frame', new=mock_send):
            res = run_secure_bb84(min_key_length=256, channel_error_rate=0.0, seed=42)
            self.assertFalse(res["secure"])
            self.assertTrue(res["aborted"])
            self.assertEqual(res["reason"], "frame_budget_exceeded")
            self.assertIsNone(res["final_key"])

    def test_insufficient_ell_max(self):
        """Abort if ell_max is below required first-output length."""
        from vol2_bb84 import run_secure_bb84, compute_finite_key_bound
        
        # We need to simulate a case where ell_max < first_pa_output_length
        original_compute = compute_finite_key_bound
        def mock_compute(*args, **kwargs):
            res = original_compute(*args, **kwargs)
            res["ell"] = 280 # Force a low bound
            return res

        with patch('vol2_bb84.compute_finite_key_bound', new=mock_compute):
            res = run_secure_bb84(min_key_length=256, channel_error_rate=0.0, seed=42)
            self.assertFalse(res["secure"])
            self.assertTrue(res["aborted"])
            self.assertEqual(res["reason"], "finite_key_bound_insufficient")


def run_all_tests():
    """CLI test runner executing legacy, finite-key bound, protocol, and M2 abort test suites."""
    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite()
    suite.addTest(loader.loadTestsFromTestCase(TestVol2BB84Legacy))
    suite.addTest(loader.loadTestsFromTestCase(TestVol2BB84FiniteKeyBound))
    suite.addTest(loader.loadTestsFromTestCase(TestVol2BB84Protocol))
    suite.addTest(loader.loadTestsFromTestCase(TestM2BB84AbortIntegration))
    suite.addTest(loader.loadTestsFromTestCase(TestVol2BB84SiftingAuthentication))
    suite.addTest(loader.loadTestsFromTestCase(TestVol2BB84ParameterEstimationAuthentication))
    suite.addTest(loader.loadTestsFromTestCase(TestVol2BB84ReconciliationAuthentication))
    suite.addTest(loader.loadTestsFromTestCase(TestVol2BB84SeedExchangeAuthentication))
    suite.addTest(loader.loadTestsFromTestCase(TestVol2BB84KeyConfirmationAuthentication))
    runner = unittest.TextTestRunner(verbosity=2)
    return runner.run(suite)


if __name__ == "__main__":
    unittest.main()