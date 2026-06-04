"""Tests for ``src.upstreamproxy.protobuf_codec``."""

from __future__ import annotations

import pytest

from src.upstreamproxy.protobuf_codec import (
    WIRE_TAG_FIELD1_LEN_DELIMITED,
    decode_chunk,
    encode_chunk,
)


class TestEncodeChunk:
    def test_empty_payload(self) -> None:
        out = encode_chunk(b'')
        assert out == bytes([0x0A, 0x00])

    def test_single_byte_payload(self) -> None:
        out = encode_chunk(b'X')
        assert out == bytes([0x0A, 0x01, ord('X')])

    def test_one_byte_varint_127(self) -> None:
        """127 is the max value that fits in a single varint byte."""
        out = encode_chunk(b'A' * 127)
        assert out[0] == 0x0A
        assert out[1] == 127  # one varint byte; high bit clear
        assert out[2:] == b'A' * 127

    def test_two_byte_varint_128(self) -> None:
        """128 takes 2 varint bytes (high bit set on the first)."""
        out = encode_chunk(b'B' * 128)
        assert out[0] == 0x0A
        assert out[1] == 0x80  # low 7 bits = 0, continuation bit set
        assert out[2] == 0x01  # high bits = 1, no continuation
        assert out[3:] == b'B' * 128

    def test_three_byte_varint_16384(self) -> None:
        """16384 = 2^14 takes 3 varint bytes."""
        n = 16384
        out = encode_chunk(b'C' * n)
        assert out[0] == 0x0A
        assert out[1] == 0x80
        assert out[2] == 0x80
        assert out[3] == 0x01
        assert len(out) == 4 + n


class TestDecodeChunk:
    def test_decode_empty_keepalive(self) -> None:
        """Empty input is the server keepalive — return empty bytes."""
        assert decode_chunk(b'') == b''

    def test_decode_zero_length_chunk(self) -> None:
        """0x0a 0x00 — well-formed chunk with empty payload."""
        assert decode_chunk(bytes([0x0A, 0x00])) == b''

    def test_decode_single_byte(self) -> None:
        assert decode_chunk(bytes([0x0A, 0x01, ord('X')])) == b'X'

    def test_decode_two_byte_varint(self) -> None:
        payload = b'B' * 128
        encoded = bytes([0x0A, 0x80, 0x01]) + payload
        assert decode_chunk(encoded) == payload

    def test_decode_wrong_tag_returns_none(self) -> None:
        """First byte must be 0x0a; anything else is malformed."""
        assert decode_chunk(bytes([0x05, 0x00])) is None
        assert decode_chunk(bytes([0xFF, 0x00])) is None

    def test_decode_truncated_payload_returns_none(self) -> None:
        """Declared length > remaining buffer."""
        assert decode_chunk(bytes([0x0A, 0x05, ord('A'), ord('B')])) is None

    def test_decode_truncated_varint_returns_none(self) -> None:
        """Continuation bit set on the last byte — varint never terminates."""
        # 0x80 0x80 0x80 (all continuation bits) — runs off the end.
        assert decode_chunk(bytes([0x0A, 0x80, 0x80, 0x80])) is None

    def test_decode_overflowed_varint_returns_none(self) -> None:
        """Varint shift > 28 means a 5+-byte length — out of bounds."""
        # 5 bytes all with continuation bit set, then one without.
        encoded = bytes([0x0A, 0x80, 0x80, 0x80, 0x80, 0x80, 0x01])
        # Should reject before the final byte (shift exceeds limit).
        assert decode_chunk(encoded) is None


class TestRoundTrip:
    @pytest.mark.parametrize('size', [0, 1, 7, 127, 128, 16383, 16384, 65536])
    def test_round_trip(self, size: int) -> None:
        original = bytes(range(256)) * (size // 256) + bytes(range(size % 256))
        # Adjust size: the construction above doesn't quite equal `size`
        # for sizes that aren't multiples of 256.
        original = original[:size] if len(original) >= size else original + b'\x00' * (
            size - len(original)
        )
        assert len(original) == size
        encoded = encode_chunk(original)
        decoded = decode_chunk(encoded)
        assert decoded == original

    def test_concat_chunks_decoded_independently(self) -> None:
        """Two chunks concatenated: decoder reads the first; second is leftover."""
        a = encode_chunk(b'first')
        b = encode_chunk(b'second')
        assert decode_chunk(a + b) == b'first'
        # Decoder doesn't return leftover; consumers split on encode.


def test_tag_constant_is_correct() -> None:
    """Tag = (field_number << 3) | wire_type = (1 << 3) | 2 = 0x0a."""
    assert WIRE_TAG_FIELD1_LEN_DELIMITED == 0x0A
    assert WIRE_TAG_FIELD1_LEN_DELIMITED == (1 << 3) | 2
