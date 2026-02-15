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
    """Compare two byte streams chunk-by-chunk."""
    left_iter = left.__aiter__()
    right_iter = right.__aiter__()

    while True:
        try:
            left_chunk = await left_iter.__anext__()
            left_done = False
        except StopAsyncIteration:
            left_chunk = b""
            left_done = True

        try:
            right_chunk = await right_iter.__anext__()
            right_done = False
        except StopAsyncIteration:
            right_chunk = b""
            right_done = True

        if left_done and right_done:
            return True
        if left_done != right_done:
            return False
        if left_chunk != right_chunk:
            return False
