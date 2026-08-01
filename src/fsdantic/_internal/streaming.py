"""Streaming helpers for chunked reads and comparisons."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator


async def hash_stream(
    stream: AsyncIterator[bytes],
    *,
    algorithm: str = "sha256",
) -> str:
    """Hash a byte stream incrementally and return the digest hex string."""
    digest = hashlib.new(algorithm)
    async for chunk in stream:
        if chunk:
            digest.update(chunk)
    return digest.hexdigest()


async def compare_streams(
    left: AsyncIterator[bytes],
    right: AsyncIterator[bytes],
) -> bool:
    """Compare two byte streams for content equality regardless of chunk boundaries.

    The comparison uses a carry-over buffer, so identical content split into
    different chunk sizes on each side compares equal (``[b"abc", b"def"]``
    vs ``[b"ab", b"cdef"]`` -> True).  Empty chunks and uneven lengths are
    handled correctly.
    """
    left_iter = left.__aiter__()
    right_iter = right.__aiter__()

    async def _next(iterator):
        try:
            return await iterator.__anext__()
        except StopAsyncIteration:
            return None

    l_pending = await _next(left_iter)
    r_pending = await _next(right_iter)

    while l_pending is not None or r_pending is not None:
        if l_pending is None or r_pending is None:
            return False  # one stream exhausted, other not

        n = min(len(l_pending), len(r_pending))
        if l_pending[:n] != r_pending[:n]:
            return False
        l_pending = l_pending[n:]
        r_pending = r_pending[n:]

        if not l_pending:
            l_pending = await _next(left_iter)
        if not r_pending:
            r_pending = await _next(right_iter)

    return True
