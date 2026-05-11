"""CCR Bridge v2 read/write transports.

Two halves:

  * ``sse_transport.SSETransport`` — read side (SSE long-poll).
  * ``ccr_client.CCRClient`` — write side (HTTP POST batching + heartbeat).

Both consumed by ``src.bridge.repl_bridge_transport.create_v2_repl_transport``.
"""
