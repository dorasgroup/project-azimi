"""Global crawler-only admission across prefork workers, with expiring leases."""
import asyncio
import hashlib
from contextlib import asynccontextmanager
from uuid import uuid4
from redis.asyncio import Redis
from redis.exceptions import RedisError

ACQUIRE = '''
local t=redis.call('TIME')
local now=t[1]*1000+math.floor(t[2]/1000)
redis.call('ZREMRANGEBYSCORE',KEYS[1],'-inf',now)
redis.call('ZREMRANGEBYSCORE',KEYS[2],'-inf',now-60000)
if redis.call('ZCARD',KEYS[1]) >= tonumber(ARGV[2]) or redis.call('ZCARD',KEYS[2]) >= tonumber(ARGV[3]) then return 0 end
redis.call('ZADD',KEYS[1],now+30000,ARGV[1])
redis.call('ZADD',KEYS[2],now,ARGV[1])
redis.call('PEXPIRE',KEYS[1],35000)
redis.call('PEXPIRE',KEYS[2],70000)
return 1
'''


class WorkerAdmission:
    def __init__(self, redis_url: str, worker_url: str, *, concurrency=12, requests_per_minute=480):
        self._redis_url = redis_url
        prefix = 'rasad:crawler:admission:' + hashlib.sha256(worker_url.encode()).hexdigest()[:20]
        self._keys = (prefix + ':active', prefix + ':starts')
        self._concurrency, self._rate = concurrency, requests_per_minute

    @asynccontextmanager
    async def slot(self):
        token = str(uuid4())
        async with Redis.from_url(self._redis_url, socket_connect_timeout=2, socket_timeout=2) as redis:
            try:
                async with asyncio.timeout(12):
                    while not await redis.eval(ACQUIRE, 2, *self._keys, token, self._concurrency, self._rate):
                        await asyncio.sleep(.1)
            except TimeoutError:
                raise TimeoutError('Crawler Worker admission is temporarily busy') from None
            except RedisError:
                raise OSError('Crawler Worker admission is unavailable') from None
            cancelled = False
            try:
                yield
            except asyncio.CancelledError:
                cancelled = True
                raise
            finally:
                # A cancelled HTTP client may leave origin work alive briefly;
                # keep that lease until expiry rather than overbook the Worker.
                if not cancelled:
                    try:
                        await redis.zrem(self._keys[0], token)
                    except RedisError:
                        # Never discard a completed HTTP response because lease
                        # release failed; the bounded lease expires automatically.
                        pass
