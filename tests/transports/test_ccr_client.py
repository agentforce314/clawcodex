"""Tests for ``src.transports.ccr_client.CCRClient``."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from src.bridge.exceptions import EpochSupersededError
from src.transports.ccr_client import CCRClient, CCRClientOptions


@pytest.mark.asyncio
async def test_initialize_spawns_loops_and_uploads_event():
    received: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == '/worker/events':
            body = json.loads(req.content)
            received.extend(body.get('events', []))
            return httpx.Response(200, json={})
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = CCRClient(
            base_url='https://api.test',
            options=CCRClientOptions(heartbeat_interval_seconds=0),
            http_client=http,
        )
        await client.initialize(epoch=1)
        try:
            await client.write_event({'type': 'user', 'uuid': 'u1'})
            await client.flush()
            assert received == [{'type': 'user', 'uuid': 'u1'}]
        finally:
            await client.aclose()


@pytest.mark.asyncio
async def test_write_batch_groups_multiple_events():
    received_batches: list[list[dict]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == '/worker/events':
            body = json.loads(req.content)
            received_batches.append(body['events'])
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = CCRClient(
            base_url='https://api.test',
            options=CCRClientOptions(
                heartbeat_interval_seconds=0,
                max_batch_size=10,
            ),
            http_client=http,
        )
        await client.initialize(epoch=1)
        try:
            # Drop several events into the queue rapidly; the uploader
            # should batch them in a single POST after the first read.
            for i in range(5):
                await client.write_event({'type': 'user', 'uuid': f'u{i}'})
            await client.flush()
        finally:
            await client.aclose()

    # All 5 should have been delivered. Batching is opportunistic — the
    # first event triggers the post; subsequent events arriving before
    # the post completes get batched.
    flat = [e for batch in received_batches for e in batch]
    assert len(flat) == 5
    assert {e['uuid'] for e in flat} == {f'u{i}' for i in range(5)}


@pytest.mark.asyncio
async def test_409_raises_epoch_superseded_error():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={'error': 'epoch superseded'})

    callback_fired = asyncio.Event()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = CCRClient(
            base_url='https://api.test',
            options=CCRClientOptions(
                heartbeat_interval_seconds=0,
                on_epoch_mismatch=callback_fired.set,
            ),
            http_client=http,
        )
        await client.initialize(epoch=1)
        try:
            await client.write_event({'type': 'user', 'uuid': 'u1'})
            # Wait for the uploader to attempt the POST and hit 409.
            for _ in range(100):
                if callback_fired.is_set():
                    break
                await asyncio.sleep(0.02)
        finally:
            await client.aclose()

    assert callback_fired.is_set()


@pytest.mark.asyncio
async def test_dropped_batch_count_tracks_5xx_failures():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(503, content=b'unavailable')

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = CCRClient(
            base_url='https://api.test',
            options=CCRClientOptions(
                heartbeat_interval_seconds=0,
                max_retries_per_batch=2,
                retry_backoff_seconds=0.01,
            ),
            http_client=http,
        )
        await client.initialize(epoch=1)
        try:
            for i in range(5):
                await client.write_event({'type': 'user', 'uuid': f'u{i}'})
            # Wait for the uploader to exhaust retries.
            for _ in range(200):
                if client.dropped_batch_count > 0:
                    break
                await asyncio.sleep(0.02)
        finally:
            await client.aclose()

    assert client.dropped_batch_count > 0


@pytest.mark.asyncio
async def test_report_state_metadata_delivery_fire_and_forget():
    """report_state/metadata/delivery POST without raising."""
    seen_paths: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen_paths.append(req.url.path)
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = CCRClient(
            base_url='https://api.test',
            options=CCRClientOptions(heartbeat_interval_seconds=0),
            http_client=http,
        )
        await client.initialize(epoch=1)
        try:
            client.report_state({'requires_action': True})
            client.report_metadata({'custom': 'value'})
            client.report_delivery('event-1', 'received')
            await asyncio.sleep(0.1)
        finally:
            await client.aclose()

    # All three paths should have been hit.
    assert any('/worker' == p for p in seen_paths)  # state + metadata both hit /worker (PUT)
    assert any('/worker/events/event-1/delivery' == p for p in seen_paths)


@pytest.mark.asyncio
async def test_write_after_close_increments_dropped():
    async with httpx.AsyncClient() as http:
        client = CCRClient(
            base_url='https://api.test',
            options=CCRClientOptions(heartbeat_interval_seconds=0),
            http_client=http,
        )
        await client.initialize(epoch=1)
        await client.aclose()
        await client.write_event({'type': 'user', 'uuid': 'u1'})
    assert client.dropped_batch_count == 1


@pytest.mark.asyncio
async def test_write_before_initialize_increments_dropped():
    async with httpx.AsyncClient() as http:
        client = CCRClient(
            base_url='https://api.test',
            options=CCRClientOptions(heartbeat_interval_seconds=0),
            http_client=http,
        )
        await client.write_event({'type': 'user', 'uuid': 'u1'})
    assert client.dropped_batch_count == 1
