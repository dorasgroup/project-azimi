"""Polluted catalogues must not hide operational sources behind fixtures."""
from datetime import UTC, datetime
from uuid import uuid4
import pytest
from sqlalchemy import select
from app.db.session import SessionFactory, engine
from app.models import Source, SourceType
from app.modules.crawling.domain.source_catalogue import is_operational_source_url
from app.modules.crawling.infrastructure.source_repository import SqlAlchemySourceRepository
from test_phase9_native_cutover import fixture_job


@pytest.mark.parametrize('url', [
    'https://example.com/a', 'https://sub.example.org/a', 'https://sub.example.net',
    'HTTPS://EXAMPLE.COM.:443/a', 'https://news.test', 'https://news.invalid',
    'https://localhost', 'http://127.0.0.1:9999/', 'http://[::1]/',
    'http://10.1.2.3/', 'http://192.168.1.1', 'https://news.local/',
    'http://169.254.169.254/', 'ftp://news.ir/', 'https://user:password@news.ir/',
    'http://[bad', 'https://news.ir:99999/', 'https://intranet/',
])
def test_nonoperational_addresses(url):
    assert not is_operational_source_url(url)


@pytest.mark.parametrize('url', [
    'https://www.irna.ir/', 'https://snn.ir/', 'https://news.ir/custom',
    'https://example.com.real.ir/', 'https://test-real.ir/', 'http://8.8.8.8/',
])
def test_real_addresses_are_not_hidden_by_names_or_substrings(url):
    assert is_operational_source_url(url)


@pytest.mark.asyncio
async def test_filter_before_pagination_preserves_fixtures_and_default_api():
    job_id, _ = await fixture_job()
    from app.models import MonitoringJob
    async with SessionFactory() as session, session.begin():
        owner = (await session.get(MonitoringJob, job_id)).requested_by
        timestamp = datetime.now(UTC)
        fixtures = [Source(title='Synthetic', base_url=f'https://{uuid4().hex}.example.org/',
                           type=SourceType.NEWS_SITE, created_by=owner, created_at=timestamp)
                    for _ in range(55)]
        real = [Source(title='Test is a legitimate title', base_url=f'https://{uuid4().hex}.news.ir/',
                       type=SourceType.NEWS_SITE, created_by=owner) for _ in range(2)]
        session.add_all(fixtures + real)
        await session.flush()
        fixture_ids, real_ids = {s.id for s in fixtures}, {s.id for s in real}
    async with SessionFactory() as session:
        repo = SqlAlchemySourceRepository(session)
        all_rows, total = await repo.list(1, 100)
        filtered, operational_total = await repo.list(1, 100, operational_only=True)
        assert fixture_ids <= {s.id for s in all_rows}
        assert real_ids <= {s.id for s in filtered}
        assert not fixture_ids & {s.id for s in filtered}
        assert total >= operational_total + 55
        first, _ = await repo.list(1, 1, operational_only=True)
        second, _ = await repo.list(2, 1, operational_only=True)
        assert first[0].id != second[0].id
        assert [first[0].id, second[0].id] == [s.id for s in filtered[:2]]
        assert all(is_operational_source_url(s.base_url) for s in filtered)
        assert len(list((await session.scalars(select(Source).where(Source.id.in_(fixture_ids)))).all())) == 55
    await engine.dispose()
