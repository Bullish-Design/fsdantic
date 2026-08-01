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


@pytest.mark.asyncio
async def test_compare_streams_different_chunk_boundaries():
    """Identical content split differently compares equal (H2)."""
    assert await compare_streams(_stream([b"abc", b"def"]), _stream([b"ab", b"cdef"])) is True
    assert await compare_streams(_stream([b"a", b"b", b"c", b"d"]), _stream([b"abcd"])) is True


@pytest.mark.asyncio
async def test_compare_streams_different_boundaries_unequal():
    """Same split, different byte content compares unequal."""
    assert await compare_streams(_stream([b"abc", b"def"]), _stream([b"ab", b"cef"])) is False


@pytest.mark.asyncio
async def test_compare_streams_empty_chunks_interleaved():
    """Empty chunks are skipped without breaking the comparison."""
    assert await compare_streams(_stream([b"a", b"", b"b"]), _stream([b"ab"])) is True
    assert await compare_streams(_stream([b"a", b"", b"b"]), _stream([b"ac"])) is False


@pytest.mark.asyncio
async def test_compare_streams_uneven_lengths():
    """One stream exhausted while the other still has bytes -> False."""
    assert await compare_streams(_stream([b"ab", b"cd"]), _stream([b"ab"])) is False
    assert await compare_streams(_stream([b"ab"]), _stream([b"ab", b"cd"])) is False


@pytest.mark.asyncio
async def test_compare_streams_empty_streams():
    assert await compare_streams(_stream([]), _stream([])) is True
    assert await compare_streams(_stream([]), _stream([b"x"])) is False
