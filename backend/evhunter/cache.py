import json
import time
from collections import defaultdict

from redis import Redis
from sqlalchemy import select

from .db import CurrentQuote


class Cache:
    def __init__(self, config):
        self.redis = Redis.from_url(config.redis_url, socket_timeout=2, socket_connect_timeout=2, decode_responses=True) if config.redis_url else None
        self.attempts = defaultdict(list)
        if self.redis and config.app_env == "production":
            self.redis.ping()

    def get(self, key):
        return self.redis.get(key) if self.redis else None

    def publish_quotes(self, session, mode, ttl):
        if not self.redis:
            return
        data = [q.data for q in session.scalars(select(CurrentQuote).where(CurrentQuote.mode == mode)).all()]
        self.redis.set(f"evhunter:{mode}:quotes", json.dumps(data), ex=ttl)

        self.redis.publish(f"evhunter:{mode}:updates", "quotes")

    def login_allowed(self, address):
        key = f"evhunter:login:{address}"
        if self.redis:
            # INCR+EXPIRE must be atomic, including after process crashes.
            count = self.redis.eval("local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],300) end; return n", 1, key)
            return count <= 10
        now = time.monotonic()
        recent = [t for t in self.attempts[address] if now-t < 300]
        self.attempts[address] = [*recent, now]
        return len(recent) < 10
