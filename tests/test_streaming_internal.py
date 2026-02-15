"""Tests for internal streaming helpers."""

import pytest

from fsdantic._internal.streaming import compare_streams, hash_stream


async def _stream(chunks: list[bytes]):
    for chunk in chunks:
        yield chunk


@pytest.mark.asyncio
async def test_hash_stream_matches_concatenated_payload_digest():
    chunks = [b"ab", b"cd", b"ef"]
    stream_digest = await hash_stream(_stream(chunks))
    full_digest = await hash_stream(_stream([b"".join(chunks)]))
    assert stream_digest == full_digest


@pytest.mark.asyncio
async def test_compare_streams_detects_equal_and_unequal():
    assert await compare_streams(_stream([b"ab", b"cd"]), _stream([b"ab", b"cd"])) is True
    assert await compare_streams(_stream([b"ab", b"cX"]), _stream([b"ab", b"cd"])) is False
    assert await compare_streams(_stream([b"ab"]), _stream([b"ab", b"cd"])) is False
