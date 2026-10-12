"""Firestore (Native mode) DocDB adapter. Connects to the emulator automatically when FIRESTORE_EMULATOR_HOST is set."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from app.storage.docdb import Where

_BATCH_LIMIT = 400


class FirestoreDocDB:
    def __init__(self, project: str | None, database: str = "(default)") -> None:
        self.client = firestore.AsyncClient(project=project or None, database=database)

    async def get(self, path: str, doc_id: str) -> dict[str, Any] | None:
        snap = await self.client.collection(path).document(doc_id).get()
        return snap.to_dict() if snap.exists else None

    async def set(self, path: str, doc_id: str, data: dict[str, Any]) -> None:
        await self.client.collection(path).document(doc_id).set(data)

    async def update(self, path: str, doc_id: str, data: dict[str, Any]) -> None:
        await self.client.collection(path).document(doc_id).update(data)

    async def delete(self, path: str, doc_id: str) -> None:
        await self.client.collection(path).document(doc_id).delete()

    async def add(self, path: str, data: dict[str, Any]) -> str:
        _, ref = await self.client.collection(path).add(data)
        return str(ref.id)

    async def query(
        self,
        path: str,
        where: Sequence[Where] = (),
        order_by: str | None = None,
        descending: bool = False,
        limit: int | None = None,
    ) -> list[tuple[str, dict[str, Any]]]:
        q: Any = self.client.collection(path)
        for field, op, value in where:
            q = q.where(filter=FieldFilter(field, op, value))
        if order_by is not None:
            direction = firestore.Query.DESCENDING if descending else firestore.Query.ASCENDING
            q = q.order_by(order_by, direction=direction)
        if limit is not None:
            q = q.limit(limit)
        return [(snap.id, snap.to_dict()) async for snap in q.stream()]

    async def delete_all(self, path: str) -> int:
        deleted = 0
        while True:
            refs = [doc.reference async for doc in self.client.collection(path).limit(_BATCH_LIMIT).stream()]
            if not refs:
                return deleted
            batch = self.client.batch()
            for ref in refs:
                batch.delete(ref)
            await batch.commit()
            deleted += len(refs)

    async def ping(self) -> bool:
        try:
            await asyncio.wait_for(self.client.collection("_health").document("ping").get(), timeout=3)
            return True
        except Exception:
            return False
