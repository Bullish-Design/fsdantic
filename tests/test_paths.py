"""Tests for internal path normalization helpers and path invariants."""

from hypothesis import given, strategies as st

from fsdantic._internal.paths import normalize_glob_pattern, normalize_path


class TestNormalizePath:
    def test_normalize_absolute_relative_and_segments(self):
        assert normalize_path("a/b") == "/a/b"
        assert normalize_path("/a/./b/../c") == "/a/c"

    def test_normalize_separators_duplicate_and_trailing(self):
        assert normalize_path(r"\\a\\b\\") == "/a/b"
        assert normalize_path("//a///b//") == "/a/b"
        assert normalize_path("/") == "/"

    @given(st.text(min_size=0, max_size=80))
    def test_idempotent(self, raw_path):
        normalized = normalize_path(raw_path)
        assert normalize_path(normalized) == normalized


class TestNormalizeGlobPattern:
    def test_normalize_glob_preserves_wildcards(self):
        assert normalize_glob_pattern(r"src\\**\\*.py") == "src/**/*.py"
        assert normalize_glob_pattern("./src//*.py") == "src/*.py"

    @given(st.text(min_size=0, max_size=80))
    def test_glob_normalization_idempotent(self, raw_pattern):
        normalized = normalize_glob_pattern(raw_pattern)
        assert normalize_glob_pattern(normalized) == normalized
