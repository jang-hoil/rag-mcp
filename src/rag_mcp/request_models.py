"""Typed request payloads for metadata and search filters."""
from __future__ import annotations

from collections.abc import Mapping
from typing import TypeAlias

from pydantic import BaseModel, Field

JsonAtom: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonAtom | list[str] | list[int] | list[float] | list[bool] | dict[str, JsonAtom | list[str] | list[int] | list[float] | list[bool]]
FilterValue: TypeAlias = str | int | float | bool | None


class DocumentMetadata(BaseModel):
    """Document-level metadata stored under Chunk.meta and Manifest.meta."""

    values: dict[str, JsonValue] = Field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw: Mapping[str, JsonValue] | "DocumentMetadata" | None) -> "DocumentMetadata":
        if raw is None:
            return cls()
        if isinstance(raw, cls):
            return raw
        return cls(values=dict(raw))

    def to_payload(self) -> dict[str, JsonValue] | None:
        return dict(self.values) if self.values else None


class SearchFilters(BaseModel):
    """Validated Qdrant filter values. Filters must be scalar MatchValue inputs."""

    values: dict[str, FilterValue] = Field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw: Mapping[str, JsonValue] | "SearchFilters" | None) -> "SearchFilters":
        if raw is None:
            return cls()
        if isinstance(raw, cls):
            return raw
        values: dict[str, FilterValue] = {}
        for key, value in raw.items():
            if isinstance(value, list | dict):
                raise ValueError(f"필터 값은 scalar만 허용: {key}={value!r}")
            values[key] = value
        return cls(values=values)

    def ensure_allowed(self, allowed_keys: frozenset[str]) -> None:
        unknown = {
            key for key in self.values
            if key not in allowed_keys and not key.startswith("meta.")
        }
        if unknown:
            raise ValueError(
                f"허용되지 않은 필터 키: {sorted(unknown)} "
                f"(허용: {sorted(allowed_keys)} 또는 meta.<키>)"
            )

    def to_qdrant(self) -> dict[str, FilterValue] | None:
        return dict(self.values) if self.values else None