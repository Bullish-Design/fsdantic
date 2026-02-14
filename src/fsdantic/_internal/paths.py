"""Path normalization helpers used by fsdantic internals."""

from __future__ import annotations


def normalize_separators(path: str) -> str:
    """Normalize all path separators to POSIX style."""
    return path.replace("\\", "/")


def collapse_duplicate_slashes(path: str) -> str:
    """Collapse repeated path separators into single slashes."""
    if not path:
        return path

    collapsed: list[str] = []
    previous_was_slash = False
    for char in path:
        if char == "/":
            if previous_was_slash:
                continue
            previous_was_slash = True
        else:
            previous_was_slash = False
        collapsed.append(char)
    return "".join(collapsed)


def _cleanup_dot_segments(segments: list[str], *, absolute: bool) -> list[str]:
    cleaned: list[str] = []
    for segment in segments:
        if segment in ("", "."):
            continue

        if segment == "..":
            if cleaned and cleaned[-1] != "..":
                cleaned.pop()
                continue
            if not absolute:
                cleaned.append(segment)
            continue

        cleaned.append(segment)

    return cleaned


def normalize_path(
    path: str,
    *,
    absolute: bool = True,
    preserve_trailing_slash: bool = False,
) -> str:
    """Normalize paths for AgentFS operations and display.

    Rules:
    - Normalize separators to '/'
    - Collapse duplicate slashes
    - Resolve '.' and '..' segments
    - Return absolute paths by default
    - Strip trailing slash except for root
    """
    normalized = collapse_duplicate_slashes(normalize_separators(path.strip()))
    is_absolute = normalized.startswith("/")

    if absolute:
        is_absolute = True

    parts = normalized.split("/") if normalized else []
    cleaned_parts = _cleanup_dot_segments(parts, absolute=is_absolute)

    if is_absolute:
        result = "/" + "/".join(cleaned_parts)
    else:
        result = "/".join(cleaned_parts)

    if not result:
        result = "/" if is_absolute else "."

    if preserve_trailing_slash and result not in ("", "/"):
        if normalized.endswith("/"):
            result += "/"

    if not preserve_trailing_slash and result != "/":
        result = result.rstrip("/")

    return result


def join_normalized_path(base: str, child: str) -> str:
    """Join two paths and return a normalized absolute path."""
    return normalize_path(f"{base.rstrip('/')}/{child}")


def normalize_glob_pattern(pattern: str) -> str:
    """Normalize path-like parts of a glob pattern.

    Keeps wildcard tokens intact while applying separator normalization,
    duplicate slash collapse, and '.'/'..' cleanup.
    """
    normalized = collapse_duplicate_slashes(normalize_separators(pattern.strip()))
    if not normalized:
        return "*"

    absolute = normalized.startswith("/")
    segments = normalized.split("/")
    cleaned: list[str] = []

    for segment in segments:
        if segment in ("", "."):
            continue
        if segment == "..":
            if cleaned and cleaned[-1] != "..":
                cleaned.pop()
                continue
            if not absolute:
                cleaned.append(segment)
            continue
        cleaned.append(segment)

    if absolute:
        result = "/" + "/".join(cleaned)
    else:
        result = "/".join(cleaned)

    if not result:
        return "/" if absolute else "*"

    if result != "/":
        result = result.rstrip("/")

    return result
