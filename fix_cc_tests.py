import re

with open("test_classical_channel.py", "r", encoding="utf-8") as f:
    code = f.read()

code = code.replace('self.bob.receive_frame(frame)', 'self.bob.receive_frame(frame, "sifting")')
code = code.replace('self.alice.receive_frame(frame_reply)', 'self.alice.receive_frame(frame_reply, "sifting")')

code = code.replace('self.bob.receive_frame(frame_a)', 'self.bob.receive_frame(frame_a, "test")')
code = code.replace('self.alice.receive_frame(frame_b)', 'self.alice.receive_frame(frame_b, "test")')
code = code.replace('self.bob.receive_frame(bad_frame)', 'self.bob.receive_frame(bad_frame, "test")')
code = code.replace('self.bob.receive_frame(frame_bad_run)', 'self.bob.receive_frame(frame_bad_run, "sifting")')
code = code.replace('self.bob.receive_frame(frame_bad_type)', 'self.bob.receive_frame(frame_bad_type, "sifting")')
code = code.replace('self.bob.receive_frame(frame2)', 'self.bob.receive_frame(frame2, "sifting")')
code = code.replace('self.bob.receive_frame(frame1)', 'self.bob.receive_frame(frame1, "sifting")')
code = code.replace('self.bob.receive_frame(f)', 'self.bob.receive_frame(f, "test")')
code = code.replace('self.alice.receive_frame(f)', 'self.alice.receive_frame(f, "test")')

# wait, line 247: self.bob.receive_frame(bad_frame) already replaced.
# wait, line 245 bad_frame has msg_type "x", so expected is "test" since it was replaced.

# Add new test
new_test = """
    def test_unexpected_msg_type(self):
        msg = {"data": "test"}
        frame = self.alice.send_frame("unexpected", msg)
        
        with self.assertRaises(AuthenticationError) as ctx:
            self.bob.receive_frame(frame, "expected")
            
        self.assertIn("Unexpected msg_type", str(ctx.exception))
"""

code = code.replace("def test_aggregate_frame_exhaustion(self):", new_test + "\n    def test_aggregate_frame_exhaustion(self):")

with open("test_classical_channel.py", "w", encoding="utf-8") as f:
    f.write(code)
