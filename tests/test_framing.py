"""第 1 周 A 的帧编解码单元测试：半包、粘包、空 body、64KB 边界、非法 JSON、VER 不匹配。"""

import struct
import unittest

from protocol import framing
from protocol.errors import E_BAD_REQUEST, E_FRAME_TOO_LARGE, IncompleteFrame, ProtocolError


def raw_frame(length: int, ver: int = 1, body: bytes = b"") -> bytes:
    return struct.pack(">IB", length, ver) + body


class FramingRoundTripTest(unittest.TestCase):
    def test_round_trip_keeps_chinese_text(self):
        msg = {"type": "JOIN", "nickname": "张三", "n": 1}
        frame = framing.encode(msg)
        decoded, rest = framing.decode(frame)
        self.assertEqual(decoded, msg)
        self.assertEqual(rest, b"")

    def test_encode_rejects_over_limit_message(self):
        big = {"pad": "a" * framing.MAX_FRAME_BYTES}
        with self.assertRaises(ProtocolError) as ctx:
            framing.encode(big)
        self.assertEqual(ctx.exception.code, E_FRAME_TOO_LARGE)


class FramingPartialFrameTest(unittest.TestCase):
    def test_incomplete_length_header(self):
        frame = framing.encode({"a": 1})
        with self.assertRaises(IncompleteFrame):
            framing.decode(frame[:3])

    def test_incomplete_version_byte(self):
        frame = framing.encode({"a": 1})
        with self.assertRaises(IncompleteFrame):
            framing.decode(frame[:4])

    def test_half_packet_then_finish(self):
        frame = framing.encode({"type": "PING", "seq": 7})
        with self.assertRaises(IncompleteFrame):
            framing.decode(frame[:6])
        decoded, rest = framing.decode(frame)
        self.assertEqual(decoded["seq"], 7)
        self.assertEqual(rest, b"")


class FramingStickyPacketTest(unittest.TestCase):
    def test_multiple_frames_in_one_buffer(self):
        first = framing.encode({"type": "PING", "seq": 1})
        second = framing.encode({"type": "PONG", "seq": 2})
        third = framing.encode({"type": "PONG", "seq": 3})
        buf = first + second + third

        decoded1, buf = framing.decode(buf)
        decoded2, buf = framing.decode(buf)
        decoded3, rest = framing.decode(buf)

        self.assertEqual([decoded1["seq"], decoded2["seq"], decoded3["seq"], rest], [1, 2, 3, b""])

    def test_rest_is_preserved_for_next_round(self):
        frame = framing.encode({"type": "PING"})
        decoded, rest = framing.decode(frame + b"partial")
        self.assertEqual(decoded["type"], "PING")
        self.assertEqual(rest, b"partial")


class FramingBoundaryTest(unittest.TestCase):
    def test_empty_body_decodes_to_empty_dict(self):
        decoded, rest = framing.decode(raw_frame(5))
        self.assertEqual(decoded, {})
        self.assertEqual(rest, b"")

    def test_frame_exactly_at_64kb_is_accepted(self):
        body_len = framing.MAX_FRAME_BYTES - 5
        body = b'{"x":"' + b"a" * (body_len - 8) + b'"}'  # 8 字节 JSON 骨架 + 填充
        self.assertEqual(len(body), body_len)
        frame = raw_frame(framing.MAX_FRAME_BYTES, body=body)
        decoded, rest = framing.decode(frame)
        self.assertEqual(len(decoded["x"]), body_len - 8)
        self.assertEqual(rest, b"")

    def test_frame_one_byte_over_limit_is_rejected(self):
        body_len = framing.MAX_FRAME_BYTES - 4
        body = b'{"x":"' + b"a" * (body_len - 8) + b'"}'
        self.assertEqual(len(body), body_len)
        frame = raw_frame(framing.MAX_FRAME_BYTES + 1, body=body)
        with self.assertRaises(ProtocolError) as ctx:
            framing.decode(frame)
        self.assertEqual(ctx.exception.code, E_FRAME_TOO_LARGE)

    def test_length_smaller_than_header_is_bad_request(self):
        with self.assertRaises(ProtocolError) as ctx:
            framing.decode(raw_frame(4))
        self.assertEqual(ctx.exception.code, E_BAD_REQUEST)


class FramingMalformedTest(unittest.TestCase):
    def test_invalid_json_body(self):
        body = b"{bad json"
        frame = raw_frame(5 + len(body), body=body)
        with self.assertRaises(ProtocolError) as ctx:
            framing.decode(frame)
        self.assertEqual(ctx.exception.code, E_BAD_REQUEST)

    def test_non_object_json_body(self):
        body = b"[1,2,3]"
        frame = raw_frame(5 + len(body), body=body)
        with self.assertRaises(ProtocolError) as ctx:
            framing.decode(frame)
        self.assertEqual(ctx.exception.code, E_BAD_REQUEST)

    def test_unsupported_version(self):
        frame = raw_frame(7, ver=2, body=b"{}")
        with self.assertRaises(ProtocolError) as ctx:
            framing.decode(frame)
        self.assertEqual(ctx.exception.code, E_BAD_REQUEST)


if __name__ == "__main__":
    unittest.main()
