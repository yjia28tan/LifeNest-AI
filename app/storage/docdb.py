"""Document-database port. Paths are Firestore collection paths, e.g. "sessions/{sid}/messages"."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal, Protocol

Op = Literal["==", "<", "<=", ">", ">=", "in"]
Where = tuple[str, Op, Any]


class DocDB(Protocol):
    async def get(self, path: str, doc_id: str) -> dict[str, Any] | None: ...
    async def set(self, path: str, doc_id: str, data: dict[str, Any]) -> None: ...
    async def update(self, path: str, doc_id: str, data: dict[str, Any]) -> None: ...
    async def delete(self, path: str, doc_id: str) -> None: ...
    async def add(self, path: str, data: dict[str, Any]) -> str: ...
    async def query(
        self,
        path: str,
        where: Sequence[Where] = (),
        order_by: str | None = None,
        descending: bool = False,
        limit: int | None = None,
    ) -> list[tuple[str, dict[str, Any]]]: ...
    async def delete_all(self, path: str) -> int: ...
    async def ping(self) -> bool: ...
