"""Custom exceptions for fsdantic library."""


class FsdanticError(Exception):
    """Base exception for all fsdantic errors."""

    pass


class RepositoryError(FsdanticError):
    """Error in repository operations."""

    pass


class MaterializationError(FsdanticError):
    """Error during workspace materialization."""

    pass


class MergeConflictError(FsdanticError):
    """Error due to merge conflicts."""

    def __init__(self, message: str, conflicts: list):
        super().__init__(message)
        self.conflicts = conflicts


class ValidationError(FsdanticError):
    """Error during model validation."""

    pass


class ContentSearchError(FsdanticError):
    """Error during content search operations."""

    pass
