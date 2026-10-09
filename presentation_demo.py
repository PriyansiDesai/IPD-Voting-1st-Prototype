import os
import sys
from unittest.mock import patch

# Force in-memory storage for the demo
os.environ["USE_MEMORY_STORAGE"] = "1"

from voting.voting_engine import VotingEngine, BB84SecurityError, VotingError
from voting.ballot_encoding import encode_choice
from vol2_bb84 import run_secure_bb84
from vol3_pqc import encrypt_vote

def demo():
    print("=========================================================")
    print("  M3->M4->Encryption Presentation Demo")
    print("  Note: BB84 is simulated key generation. Ballot bits")
    print("  do NOT travel through the BB84 quantum channel.")
    print("=========================================================\n")

    original_encode_choice = encode_choice
    original_run_secure_bb84 = run_secure_bb84
    original_encrypt_vote = encrypt_vote

    def run_scenario(scenario_name, bb84_seed, eavesdrop=False):
        print(f"--- Scenario: {scenario_name} ---")
        
        engine = VotingEngine()
        session_id = "SESS-004"
        engine.set_session_status(session_id, "ACTIVE")
        voter_id = "V001"
        candidate_id = "C001"
        
        # Trackers
        checks_passed = False
        encoded_length = 0
        qber = 0.0
        threshold = None
        encryption_ran = False
        ballot_stored = False

        def mock_encode_choice(*args, **kwargs):
            nonlocal checks_passed, encoded_length
            checks_passed = True # If we reach encode_choice, initial checks passed
            res = original_encode_choice(*args, **kwargs)
            encoded_length = len(res['encoded_bits'])
            return res

        def mock_run_secure_bb84(*args, **kwargs):
            nonlocal qber, threshold
            kwargs['seed'] = bb84_seed
            if eavesdrop:
                kwargs['eavesdrop'] = True
            res = original_run_secure_bb84(*args, **kwargs)
            qber = res.get('qber', 0.0)
            threshold = res.get('qber_threshold')
            return res

        def mock_encrypt_vote(*args, **kwargs):
            nonlocal encryption_ran
            encryption_ran = True
            return original_encrypt_vote(*args, **kwargs)

        initial_blocks = len(engine.get_session_chain(session_id).chain)

        with patch('voting.voting_engine.encode_choice', side_effect=mock_encode_choice), \
             patch('voting.voting_engine.run_secure_bb84', side_effect=mock_run_secure_bb84), \
             patch('voting.voting_engine.encrypt_vote', side_effect=mock_encrypt_vote):
            
            try:
                engine.cast_vote(session_id, voter_id, candidate_id)
                print("Result: Success")
            except (BB84SecurityError, VotingError) as e:
                print(f"Result: Aborted / Rejected ({e})")
            except Exception as e:
                print(f"Result: Error ({type(e).__name__}: {e})")

        final_blocks = len(engine.get_session_chain(session_id).chain)
        ballot_stored = final_blocks > initial_blocks

        print(f"  Engine validation/reservation reached M3: {checks_passed}")
        if checks_passed:
            thresh_str = f"{threshold:.2%}" if threshold is not None else "unavailable"
            print(f"  Encoded Choice Length:                    {encoded_length} bits")
            print(f"  BB84 QBER:                                {qber:.2%} (Threshold: {thresh_str})")
            print(f"  Encryption Ran:                           {encryption_ran}")
            print(f"  Ballot Stored to Ledger:                  {ballot_stored}")
        print()

    run_scenario("Normal Vote (No Eavesdropping)", bb84_seed=1001, eavesdrop=False)
    run_scenario("Eavesdropping Attack", bb84_seed=123, eavesdrop=True)

if __name__ == '__main__':
    demo()
