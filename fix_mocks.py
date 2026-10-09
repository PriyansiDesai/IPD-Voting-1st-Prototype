import re

with open("test_vol2_bb84.py", "r", encoding="utf-8") as f:
    code = f.read()

code = code.replace("def mock_receive(self_obj, frame):", "def mock_receive(self_obj, frame, expected_msg_type):")
code = code.replace("original_receive(self_obj, frame)", "original_receive(self_obj, frame, expected_msg_type)")

with open("test_vol2_bb84.py", "w", encoding="utf-8") as f:
    f.write(code)
