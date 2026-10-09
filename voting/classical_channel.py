import json
import struct
import hmac
import threading
from typing import Any

MAX_FRAMES = 4700
MAX_SERIALIZED_FRAME_BYTES = 32768

def gf2_128_mul(a: int, b: int) -> int:
    """
    Multiplication in GF(2^128) using polynomial x^128 + x^7 + x^2 + x + 1.
    Simulation only.
    """
    p = 0
    for i in range(128):
        if (b >> i) & 1:
            p ^= a
        carry = a >> 127
        a = (a << 1) & ((1 << 128) - 1)
        if carry:
            a ^= 0x87
    return p

class WegmanCarterMAC:
    """
    Wegman-Carter MAC over GF(2^128).
    Simulation implementation of universal hashing + one-time pad.
    """
    def __init__(self, hash_key: bytes):
        if len(hash_key) != 16:
            raise ValueError("Hash key must be exactly 16 bytes")
        self.H = int.from_bytes(hash_key, 'big')

    def sign(self, message: bytes, pad: bytes) -> bytes:
        if len(pad) != 16:
            raise ValueError("Pad must be exactly 16 bytes")

        padded_msg = message
        rem = len(message) % 16
        if rem != 0:
            padded_msg += b'\x00' * (16 - rem)

        # Append message length in bits as a 16-byte block
        length_block = (len(message) * 8).to_bytes(16, 'big')
        padded_msg += length_block

        Y = 0
        for i in range(0, len(padded_msg), 16):
            X = int.from_bytes(padded_msg[i:i+16], 'big')
            Y ^= X
            Y = gf2_128_mul(Y, self.H)

        T = Y ^ int.from_bytes(pad, 'big')
        return T.to_bytes(16, 'big')


def serialize_frame_data(run_id: str, sender: str, msg_type: str, seq_num: int, pad_idx: int, payload: dict) -> bytes:
    """
    Canonical frame serialization.
    """
    payload_bytes = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')
    run_id_bytes = run_id.encode('utf-8')
    sender_bytes = sender.encode('utf-8')
    msg_type_bytes = msg_type.encode('utf-8')

    # 2 bytes run_id len, 1 byte sender len, 1 byte msg_type len, 4 bytes seq_num, 4 bytes pad_idx
    header = struct.pack(
        "!HBB I I",
        len(run_id_bytes),
        len(sender_bytes),
        len(msg_type_bytes),
        seq_num,
        pad_idx
    )
    return header + run_id_bytes + sender_bytes + msg_type_bytes + payload_bytes

class AuthenticationError(Exception):
    pass

class FrameExhaustionError(Exception):
    pass

class SynchronizedFrameAllocator:
    """
    Simulation-only synchronized pad allocator.
    Guarantees no two frames can reuse the same one-time pad across the aggregate limit,
    even if both endpoints transmit concurrently before receiving.
    """
    def __init__(self, auth_key: bytes):
        required_len = 16 + MAX_FRAMES * 16
        if len(auth_key) < required_len:
            raise ValueError(f"Insufficient auth_key length. Expected at least {required_len} bytes.")

        self.hash_key = auth_key[:16]
        self.pads = auth_key[16:16 + MAX_FRAMES * 16]
        self.next_tx_idx = 0
        self.consumed_rx_indices = set()
        self.lock = threading.Lock()

    def get_hash_key(self) -> bytes:
        return self.hash_key

    def allocate_tx_pad(self) -> tuple[int, bytes]:
        with self.lock:
            if self.next_tx_idx >= MAX_FRAMES:
                raise FrameExhaustionError("Aggregate frame budget exceeded.")
            idx = self.next_tx_idx
            self.next_tx_idx += 1
            pad = self.pads[idx * 16 : (idx + 1) * 16]
            return idx, pad

    def consume_rx_pad(self, idx: int) -> bytes:
        with self.lock:
            if idx < 0 or idx >= MAX_FRAMES:
                raise FrameExhaustionError("Invalid pad index.")
            if idx in self.consumed_rx_indices:
                raise AuthenticationError(f"Pad index {idx} has already been consumed (replay/reuse attack).")
            self.consumed_rx_indices.add(idx)
            return self.pads[idx * 16 : (idx + 1) * 16]

class AuthenticatedChannelEndpoint:
    """
    Represents one endpoint of the authenticated classical channel for BB84 simulation.
    Enforces terminal abort on any error, and a strict aggregate frame budget of 4700 frames.
    """
    def __init__(self, run_id: str, my_id: str, peer_id: str, allocator: SynchronizedFrameAllocator):
        self.run_id = run_id
        self.my_id = my_id
        self.peer_id = peer_id

        if my_id not in ("alice", "bob") or peer_id not in ("alice", "bob"):
            raise ValueError("my_id and peer_id must be 'alice' and 'bob'")

        self.allocator = allocator
        self.mac = WegmanCarterMAC(self.allocator.get_hash_key())

        self.tx_seq = 0
        self.rx_seq = 0
        self.aborted = False
        self.lock = threading.Lock()

    def send_frame(self, msg_type: str, payload: dict) -> dict:
        with self.lock:
            if self.aborted:
                raise AuthenticationError("Endpoint is aborted.")

            try:
                seq = self.tx_seq
                pad_idx, pad = self.allocator.allocate_tx_pad()

                data = serialize_frame_data(self.run_id, self.my_id, msg_type, seq, pad_idx, payload)
                if len(data) > MAX_SERIALIZED_FRAME_BYTES:
                    raise AuthenticationError(f"Frame exceeds maximum serialized size limit: {len(data)} > {MAX_SERIALIZED_FRAME_BYTES}")
                tag = self.mac.sign(data, pad)

                self.tx_seq += 1

                return {
                    "run_id": self.run_id,
                    "sender": self.my_id,
                    "msg_type": msg_type,
                    "seq_num": seq,
                    "pad_idx": pad_idx,
                    "payload": payload,
                    "tag": tag.hex()
                }
            except Exception:
                self.aborted = True
                raise

    def receive_frame(self, frame: dict, expected_msg_type: str) -> dict:
        with self.lock:
            if self.aborted:
                raise AuthenticationError("Endpoint is aborted.")

            try:
                if frame.get("run_id") != self.run_id:
                    raise AuthenticationError("Invalid run_id")
                if frame.get("sender") != self.peer_id:
                    raise AuthenticationError("Invalid sender")

                seq = frame.get("seq_num")
                pad_idx = frame.get("pad_idx")
                if seq is None or pad_idx is None:
                    raise AuthenticationError("Missing sequence number or pad index")

                if seq != self.rx_seq:
                    raise AuthenticationError(f"Out of order or repeated sequence number. Expected {self.rx_seq}, got {seq}")

                pad = self.allocator.consume_rx_pad(pad_idx)

                data = serialize_frame_data(frame["run_id"], frame["sender"], frame["msg_type"], seq, pad_idx, frame["payload"])
                if len(data) > MAX_SERIALIZED_FRAME_BYTES:
                    raise AuthenticationError(f"Received frame exceeds maximum serialized size limit: {len(data)} > {MAX_SERIALIZED_FRAME_BYTES}")

                expected_tag = self.mac.sign(data, pad).hex()

                if not hmac.compare_digest(frame.get("tag", ""), expected_tag):
                    raise AuthenticationError("Authentication tag mismatch")

                if frame.get("msg_type") != expected_msg_type:
                    raise AuthenticationError(f"Unexpected msg_type: expected {expected_msg_type}, got {frame.get('msg_type')}")

                self.rx_seq += 1
                return frame["payload"]
            except Exception:
                self.aborted = True
                raise
