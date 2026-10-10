"""Private, compressed progress on shared storage; writes use lifecycle DB fences."""
import gzip
import hashlib
import json
import os
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select, func
from app.models.source_task import SourceTask
from app.shared.infrastructure.run_fence import lock_parent_for_child

MAX_RECORD_BYTES = 12 * 1024 * 1024


class CrawlCheckpointFenceLost(RuntimeError):
    pass


class FencedCrawlCheckpoint:
    """One immutable task/spec namespace; never reusable by another run or spec."""

    def __init__(self, session_factory, task_id: UUID, claim_token: UUID, spec, *, root=Path('/storage/crawl-checkpoints')):
        self._sessions = session_factory
        self._task_id, self._token = task_id, claim_token
        self._spec_digest = hashlib.sha256(json.dumps(spec.to_dict(), sort_keys=True, default=str).encode()).hexdigest()
        self._directory = Path(root) / 'v1' / str(UUID(str(task_id))) / self._spec_digest
        self._records = None

    async def _owns(self, session):
        status = await lock_parent_for_child(session, SourceTask.run_id, SourceTask.id, self._task_id)
        if status not in ('pending', 'running'):
            raise CrawlCheckpointFenceLost('Crawl checkpoint parent is no longer active')
        owner = await session.scalar(select(SourceTask.id).where(
            SourceTask.id == self._task_id, SourceTask.status == 'running',
            SourceTask.claim_token == self._token, SourceTask.lease_expires_at > func.now(),
        ).with_for_update())
        if owner is None:
            raise CrawlCheckpointFenceLost('Crawl checkpoint claim is no longer owned')

    async def load(self) -> dict[str, object]:
        if self._records is not None:
            return self._records
        async with self._sessions() as session:
            await self._owns(session)
            records = {}
            if self._directory.exists():
                for path in self._directory.glob('*.json.gz'):
                    if path.is_symlink() or path.stat().st_size > MAX_RECORD_BYTES:
                        raise ValueError('Unsafe crawl checkpoint record')
                    with gzip.open(path, 'rb') as stream:
                        raw = stream.read(MAX_RECORD_BYTES + 1)
                    if len(raw) > MAX_RECORD_BYTES:
                        raise ValueError('Crawl checkpoint record exceeds limit')
                    record = json.loads(raw)
                    if record.get('spec_digest') != self._spec_digest:
                        raise ValueError('Crawl checkpoint spec mismatch')
                    records[record['key']] = record['value']
            self._records = records
            return records

    async def save(self, key: str, value: dict[str, object]) -> None:
        raw = json.dumps({'spec_digest': self._spec_digest, 'key': key, 'value': value}, ensure_ascii=False).encode()
        if len(raw) > MAX_RECORD_BYTES:
            raise ValueError('Crawl checkpoint record exceeds limit')
        compressed = gzip.compress(raw, compresslevel=1)
        async with self._sessions() as session:
            await self._owns(session)
            self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            name = hashlib.sha256(key.encode()).hexdigest()
            target = self._directory / (name + '.json.gz')
            temporary = self._directory / (name + '.' + str(uuid4()) + '.tmp')
            try:
                with temporary.open('xb') as stream:
                    os.chmod(temporary, 0o600)
                    stream.write(compressed)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, target)
                directory_fd = os.open(self._directory, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            finally:
                temporary.unlink(missing_ok=True)
            # The file is durable before releasing the parent/task locks.
            await session.commit()
            if self._records is not None:
                self._records[key] = value

    async def discard(self) -> None:
        async with self._sessions() as session:
            await lock_parent_for_child(session, SourceTask.run_id, SourceTask.id, self._task_id)
            terminal = await session.scalar(select(SourceTask.id).where(
                SourceTask.id == self._task_id, SourceTask.status == 'completed',
            ).with_for_update())
            if terminal is None or not self._directory.exists():
                return
            if self._directory.is_symlink():
                raise ValueError('Unsafe crawl checkpoint directory')
            directory = self._directory.resolve(strict=True)
            # No recursive removal; only records in this exact UUID/spec folder.
            for record in self._directory.glob('*.json.gz'):
                if record.is_symlink() or record.resolve(strict=True).parent != directory:
                    raise ValueError('Unsafe crawl checkpoint cleanup target')
                record.unlink()
            self._records = {}
