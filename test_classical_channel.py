import unittest
import copy
import hashlib
from voting.classical_channel import (
    SynchronizedFrameAllocator,
    AuthenticatedChannelEndpoint,
    AuthenticationError,
    FrameExhaustionError,
    serialize_frame_data,
    WegmanCarterMAC,
    MAX_FRAMES
)

class TestClassicalChannel(unittest.TestCase):
    def setUp(self):
        self.run_id = "test-run-123"
        self.auth_key_len = 16 + MAX_FRAMES * 16

        # Generate distinct deterministic test pads
        material = bytearray()
        for i in range(MAX_FRAMES + 1):
            material.extend(hashlib.sha256(str(i).encode()).digest()[:16])
        self.auth_key = bytes(material)

        # Verify no repeated pads
        pads_only = self.auth_key[16:]
        blocks = [pads_only[i:i+16] for i in range(0, len(pads_only), 16)]
        self.assertEqual(len(blocks), len(set(blocks)))

        self.allocator = SynchronizedFrameAllocator(self.auth_key)
        self.alice = AuthenticatedChannelEndpoint(self.run_id, "alice", "bob", self.allocator)
        self.bob = AuthenticatedChannelEndpoint(self.run_id, "bob", "alice", self.allocator)

    def test_insufficient_key_material(self):
        short_key = b'\x00' * (self.auth_key_len - 1)
        with self.assertRaises(ValueError) as ctx:
            SynchronizedFrameAllocator(short_key)
        self.assertIn("Insufficient auth_key length", str(ctx.exception))

    def test_valid_frames(self):
        msg = {"type": "bases", "data": [0, 1, 1, 0]}
        frame = self.alice.send_frame("sifting", msg)

        self.assertEqual(frame["seq_num"], 0)
        self.assertEqual(frame["sender"], "alice")

        received_msg = self.bob.receive_frame(frame, "sifting")
        self.assertEqual(received_msg, msg)

        # Bob replies
        reply = {"type": "bases", "data": [1, 1, 0, 0]}
        frame_reply = self.bob.send_frame("sifting", reply)
        received_reply = self.alice.receive_frame(frame_reply, "sifting")
        self.assertEqual(received_reply, reply)

    def test_concurrent_send_distinct_pads(self):
        """Proves pad allocation is safe even if both send before either receives."""
        # Alice and Bob both send simultaneously
        frame_a = self.alice.send_frame("test", {"data": "alice"})
        frame_b = self.bob.send_frame("test", {"data": "bob"})

        self.assertNotEqual(frame_a["pad_idx"], frame_b["pad_idx"])
        self.assertEqual(frame_a["pad_idx"], 0)
        self.assertEqual(frame_b["pad_idx"], 1)

        # They should both be able to receive the other's frame successfully
        # because the pad_idx tells them which pad to consume for verification
        res_b = self.bob.receive_frame(frame_a, "test")
        res_a = self.alice.receive_frame(frame_b, "test")

        self.assertEqual(res_b["data"], "alice")
        self.assertEqual(res_a["data"], "bob")

        # Attempting to reuse a pad idx fails
        bad_frame = copy.deepcopy(frame_a)
        bad_frame["seq_num"] = 1 # bypass seq check
        with self.assertRaises(AuthenticationError) as ctx:
            self.bob.receive_frame(bad_frame, "test")
        self.assertIn("already been consumed", str(ctx.exception))

    def test_tampered_payload_causes_terminal_abort(self):
        msg = {"type": "bases", "data": [0, 1]}
        frame = self.alice.send_frame("sifting", msg)

        # Tamper payload
        frame["payload"]["data"][0] = 1
        with self.assertRaises(AuthenticationError) as ctx:
            self.bob.receive_frame(frame, "sifting")
        self.assertIn("Authentication tag mismatch", str(ctx.exception))

        # Bob is aborted, any further call fails
        with self.assertRaises(AuthenticationError) as ctx:
            self.bob.send_frame("test", {})
        self.assertIn("aborted", str(ctx.exception))

    def test_tampered_header(self):
        frame = self.alice.send_frame("sifting", {"data": "test"})

        # Tamper run_id
        frame_bad_run = copy.deepcopy(frame)
        frame_bad_run["run_id"] = "wrong-run"
        with self.assertRaises(AuthenticationError) as ctx:
            self.bob.receive_frame(frame_bad_run, "sifting")
        self.assertIn("Invalid run_id", str(ctx.exception))

        # Recreate bob because he is aborted
        self.bob = AuthenticatedChannelEndpoint(self.run_id, "bob", "alice", self.allocator)

        # Tamper msg_type
        frame_bad_type = copy.deepcopy(frame)
        frame_bad_type["msg_type"] = "other"
        with self.assertRaises(AuthenticationError) as ctx:
            self.bob.receive_frame(frame_bad_type, "sifting")
        self.assertIn("Authentication tag mismatch", str(ctx.exception))

    def test_replay_and_reordering(self):
        frame1 = self.alice.send_frame("sifting", {"data": "1"})
        frame2 = self.alice.send_frame("sifting", {"data": "2"})

        # Reorder: send frame2 first
        with self.assertRaises(AuthenticationError) as ctx:
            self.bob.receive_frame(frame2, "sifting")
        self.assertIn("Out of order", str(ctx.exception))

        # Bob is now aborted. Frame 1 must fail too.
        with self.assertRaises(AuthenticationError) as ctx:
            self.bob.receive_frame(frame1, "sifting")
        self.assertIn("aborted", str(ctx.exception))

    def test_multithreaded_concurrent_send(self):
        """Test safe concurrent allocation and sequence numbering from both endpoints."""
        import threading
        num_threads_per_endpoint = 50
        barrier = threading.Barrier(num_threads_per_endpoint * 2)

        alice_frames = []
        bob_frames = []

        def alice_send():
            barrier.wait()
            try:
                frame = self.alice.send_frame("test", {"data": "alice"})
                alice_frames.append(frame)
            except Exception:
                pass

        def bob_send():
            barrier.wait()
            try:
                frame = self.bob.send_frame("test", {"data": "bob"})
                bob_frames.append(frame)
            except Exception:
                pass

        threads = []
        for _ in range(num_threads_per_endpoint):
            t_a = threading.Thread(target=alice_send)
            t_b = threading.Thread(target=bob_send)
            threads.extend([t_a, t_b])
            t_a.start()
            t_b.start()

        for t in threads:
            t.join()

        # Assert no errors occurred
        self.assertEqual(len(alice_frames), num_threads_per_endpoint)
        self.assertEqual(len(bob_frames), num_threads_per_endpoint)

        all_frames = alice_frames + bob_frames
        pad_indices = [f["pad_idx"] for f in all_frames]

        # Assert every allocated pad_idx is unique
        self.assertEqual(len(pad_indices), len(set(pad_indices)))

        # Sort Alice frames by sequence number to receive them in order
        alice_frames.sort(key=lambda f: f["seq_num"])
        bob_frames.sort(key=lambda f: f["seq_num"])

        # Both endpoints' sequence numbers are valid contiguous ranges
        self.assertEqual([f["seq_num"] for f in alice_frames], list(range(num_threads_per_endpoint)))
        self.assertEqual([f["seq_num"] for f in bob_frames], list(range(num_threads_per_endpoint)))

        # The resulting frames can be received in order
        for f in alice_frames:
            res = self.bob.receive_frame(f, "test")
            self.assertEqual(res["data"], "alice")

        for f in bob_frames:
            res = self.alice.receive_frame(f, "test")
            self.assertEqual(res["data"], "bob")

    def test_multithreaded_frame_limit_enforcement(self):
        """Test concurrent allocation correctly enforces MAX_FRAMES limit."""
        import threading
        # Fast forward allocator to close to max frames
        self.allocator.next_tx_idx = MAX_FRAMES - 5

        barrier = threading.Barrier(10)
        success_count = 0
        exhaustion_errors = 0
        lock = threading.Lock()

        def worker():
            nonlocal success_count, exhaustion_errors
            barrier.wait()
            try:
                # Any endpoint can send
                self.alice.send_frame("test", {})
                with lock:
                    success_count += 1
            except FrameExhaustionError:
                with lock:
                    exhaustion_errors += 1
            except AuthenticationError:
                # Might get AuthenticationError if endpoint aborted by another thread's exhaustion
                pass

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Only exactly 5 sends should have succeeded
        self.assertEqual(success_count, 5)
        # The allocator should not have exceeded the limit
        self.assertEqual(self.allocator.next_tx_idx, MAX_FRAMES)

        # Alice is aborted because one of the threads hit exhaustion and aborted her
        self.assertTrue(self.alice.aborted)


    def test_unexpected_msg_type(self):
        msg = {"data": "test"}
        frame = self.alice.send_frame("unexpected", msg)

        with self.assertRaises(AuthenticationError) as ctx:
            self.bob.receive_frame(frame, "expected")

        self.assertIn("Unexpected msg_type", str(ctx.exception))

    def test_aggregate_frame_exhaustion(self):
        # Consume MAX_FRAMES - 1 pads
        self.allocator.next_tx_idx = MAX_FRAMES - 1

        # This takes the last pad
        frame = self.alice.send_frame("test", {})

        # Next send must fail due to exhaustion
        with self.assertRaises(FrameExhaustionError):
            self.bob.send_frame("test", {})

        # Receiving a frame with out of bounds pad_idx fails
        bad_frame = {"run_id": self.run_id, "sender": "alice", "msg_type": "x", "seq_num": 0, "pad_idx": MAX_FRAMES, "payload": {}}
        # Because Bob is now aborted from the previous error, the next call raises AuthenticationError
        with self.assertRaises(AuthenticationError) as ctx:
            self.bob.receive_frame(bad_frame, "test")
        self.assertIn("aborted", str(ctx.exception))

    def test_mac_serialization_and_key_consumption(self):
        mac = WegmanCarterMAC(self.auth_key[:16])
        pad = self.auth_key[16:32]

        data = serialize_frame_data(self.run_id, "alice", "test", 0, 0, {"a": 1})
        expected_tag = mac.sign(data, pad).hex()

        frame = self.alice.send_frame("test", {"a": 1})
        self.assertEqual(frame["tag"], expected_tag)

if __name__ == '__main__':
    unittest.main()
