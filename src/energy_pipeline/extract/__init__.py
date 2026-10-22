"""Extractors: fetch one source for one business date and return validated rows."""

from pydantic import BaseModel


class SourceFormatError(ValueError):
    """The source answered, but not in the shape documented in docs/sources.md."""


def to_rows(records: list[BaseModel]) -> list[dict]:
    return [record.model_dump() for record in records]
