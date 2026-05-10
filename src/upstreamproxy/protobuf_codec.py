"""Hand-encoded ``UpstreamProxyChunk`` protobuf codec.

Ports ``encode_chunk``/``decode_chunk`` from
``typescript/src/upstreamproxy/relay.ts:66-103``.

Wire format for ``message UpstreamProxyChunk { bytes data = 1; }``:
    tag = (field_number << 3) | wire_type = (1 << 3) | 2 = 0x0a
    followed by varint length, followed by the bytes.

The chapter calls this out as the "10-line pattern" — for a single-field
``bytes`` message, hand encoding is shorter than pulling in a protobuf
runtime, and the bit manipulation is well-contained.

A varint encodes an unsigned 64-bit length using 7 bits per byte plus a
continuation bit (0x80 means "more bytes follow"). Most chunks fit in
1-3 length bytes; bytes longer than ``MAX_CHUNK_BYTES`` (~512KB Envoy
buffer cap) are split by the relay caller, not here.
"""

from __future__ import annotations

#: Field 1, wire-type 2 (length-delimited bytes). The single byte that
#: starts every UpstreamProxyChunk on the wire.
WIRE_TAG_FIELD1_LEN_DELIMITED = 0x0A

#: Maximum varint chain length. ``2^28`` covers up to 256MB chunks
#: (4 bytes × 7 bits = 28 bits). Real chunks are <= 512KB, so 4 bytes
#: of varint is the practical max — but the loop accepts up to 5 (35
#: bits, > Envoy's 4GB hard cap on a single proto message). Matches TS
#: ``relay.ts:99`` ``shift > 28`` overflow check.
_MAX_VARINT_SHIFT = 28


def encode_chunk(data: bytes) -> bytes:
    """Encode ``data`` as a protobuf ``UpstreamProxyChunk`` message.

    Wire layout: ``[0x0a][varint(len)][data...]``. Returns ``bytes``;
    the original ``data`` is not modified.
    """
    n = len(data)
    # Varint encoding: chunks of 7 bits, low-bit-first; high bit set
    # while more bytes follow, cleared on the last byte.
    varint = bytearray()
    while n > 0x7F:
        varint.append((n & 0x7F) | 0x80)
        n >>= 7
    varint.append(n)

    out = bytearray(1 + len(varint) + len(data))
    out[0] = WIRE_TAG_FIELD1_LEN_DELIMITED
    out[1 : 1 + len(varint)] = varint
    out[1 + len(varint) :] = data
    return bytes(out)


def decode_chunk(buf: bytes) -> bytes | None:
    """Decode an ``UpstreamProxyChunk``. Returns the ``bytes`` field or ``None``.

    Returns ``None`` for malformed input (wrong tag byte, varint
    overflow, declared length exceeds remaining buffer). Tolerates a
    zero-length input by returning empty bytes — the server uses that
    as a keepalive signal.

    Mirrors ``typescript/src/upstreamproxy/relay.ts:87-103``.
    """
    if len(buf) == 0:
        # Server keepalive — empty payload, no header.
        return b''
    if buf[0] != WIRE_TAG_FIELD1_LEN_DELIMITED:
        return None

    length = 0
    shift = 0
    i = 1
    while i < len(buf):
        b = buf[i]
        length |= (b & 0x7F) << shift
        i += 1
        if (b & 0x80) == 0:
            break
        shift += 7
        if shift > _MAX_VARINT_SHIFT:
            return None
    else:
        # Loop ran off the end without seeing the continuation-bit
        # terminator — truncated varint.
        return None

    if i + length > len(buf):
        # Declared length exceeds what's left in the buffer.
        return None
    return bytes(buf[i : i + length])


__all__ = ['WIRE_TAG_FIELD1_LEN_DELIMITED', 'decode_chunk', 'encode_chunk']
