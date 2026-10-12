"""In-memory DocDB for tests and emulator-free local runs. Same semantics as the Firestore adapter."""

from __future__ import annotations

import copy
import operator
import secrets
from collections.abc import Callable, Sequence
from typing import Any

from app.storage.docdb import Op, Where

_OPS: dict[Op, Callable[[Any, Any], bool]] = {
    "==": operator.eq, "<": operator.lt, "<=": operator.le,
    ">": operator.gt, ">=": operator.ge, "in": lambda a, b: a in b,
}


class MemoryDocDB:
    def __init__(self) -> None:
        self._data: dict[str, dict[str, dict[str, Any]]] = {}

    async def get(self, path: str, doc_id: str) -> dict[str, Any] | None:
        doc = self._data.get(path, {}).get(doc_id)
        return copy.deepcopy(doc) if doc is not None else None

    async def set(self, path: str, doc_id: str, data: dict[str, Any]) -> None:
        self._data.setdefault(path, {})[doc_id] = copy.deepcopy(data)

    async def update(self, path: str, doc_id: str, data: dict[str, Any]) -> None:
        if doc_id not in self._data.get(path, {}):
            raise KeyError(f"{path}/{doc_id}")
        self._data[path][doc_id].update(copy.deepcopy(data))

    async def delete(self, path: str, doc_id: str) -> None:
        self._data.get(path, {}).pop(doc_id, None)

    async def add(self, path: str, data: dict[str, Any]) -> str:
        doc_id = secrets.token_hex(10)
        await self.set(path, doc_id, data)
        return doc_id

    async def query(
        self,
        path: str,
        where: Sequence[Where] = (),
        order_by: str | None = None,
        descending: bool = False,
        limit: int | None = None,
    ) -> list[tuple[str, dict[str, Any]]]:
        rows = [
            (doc_id, doc) for doc_id, doc in self._data.get(path, {}).items()
            if all(field in doc and _OPS[op](doc[field], value) for field, op, value in where)
        ]
        if order_by is not None:
            rows = [r for r in rows if order_by in r[1]]
            rows.sort(key=lambda r: r[1][order_by], reverse=descending)
        if limit is not None:
            rows = rows[:limit]
        return [(doc_id, copy.deepcopy(doc)) for doc_id, doc in rows]

    async def delete_all(self, path: str) -> int:
        return len(self._data.pop(path, {}))

    async def ping(self) -> bool:
        return True
