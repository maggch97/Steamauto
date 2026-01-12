import time
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from typing import Any, Dict, Optional, Tuple

import requests


@dataclass(frozen=True)
class PriceQuery:
    platform: str
    item_id: str
    min_wear: str
    max_wear: str


class PriceAPI:
    """Thin client for the local Price API.

    Responsibilities:
    - GET /api/price with retry (sleep 1s between retries)
    - 30-minute TTL in-memory cache per (platform,item_id,min_wear,max_wear)

    This client is intentionally small and self-contained for private maintenance.
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8080",
        timeout_seconds: float = 8.0,
        retries: int = 3,
        cache_ttl_seconds: int = 30 * 60,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.retries = max(1, int(retries))
        self.cache_ttl_seconds = int(cache_ttl_seconds)
        self._session = session or requests.Session()
        self._cache: Dict[PriceQuery, Tuple[float, Dict[str, Any]]] = {}

    @staticmethod
    def wear_bucket_2dp(wear: Decimal) -> Tuple[str, str]:
        """Return [min,max] wear bucket for a wear value, using 2-decimal buckets.

        Example: 0.111 -> ("0.11", "0.12")
        """
        if wear < 0 or wear > 1:
            raise ValueError("wear out of range [0,1]")
        bucket_min = (wear * Decimal(100)).to_integral_value(rounding=ROUND_DOWN) / Decimal(100)
        bucket_max = bucket_min + Decimal("0.01")
        if bucket_max > 1:
            bucket_max = Decimal("1")
        return (f"{bucket_min:.2f}", f"{bucket_max:.2f}")

    def _get_cached(self, q: PriceQuery) -> Optional[Dict[str, Any]]:
        cached = self._cache.get(q)
        if not cached:
            return None
        cached_at, payload = cached
        if time.time() - cached_at <= self.cache_ttl_seconds:
            return payload
        self._cache.pop(q, None)
        return None

    def _set_cached(self, q: PriceQuery, payload: Dict[str, Any]) -> None:
        self._cache[q] = (time.time(), payload)

    def query_raw(self, platform: str, item_id: str, min_wear: str, max_wear: str) -> Dict[str, Any]:
        q = PriceQuery(platform=platform, item_id=str(item_id), min_wear=str(min_wear), max_wear=str(max_wear))

        cached = self._get_cached(q)
        if cached is not None:
            return cached

        url = f"{self.base_url}/api/price"
        params = {
            "platform": platform,
            "item_id": str(item_id),
            "min_abrade": str(min_wear),
            "max_abrade": str(max_wear),
        }

        last_exc: Optional[Exception] = None
        for attempt in range(1, self.retries + 1):
            try:
                resp = self._session.get(url, params=params, timeout=self.timeout_seconds)
                resp.raise_for_status()
                payload = resp.json()
                if isinstance(payload, dict):
                    self._set_cached(q, payload)
                    return payload
                raise ValueError("price api returned non-object json")
            except Exception as e:
                last_exc = e
                if attempt < self.retries:
                    time.sleep(1)
                else:
                    raise
        raise last_exc or RuntimeError("price api failed")

    def query_cheapest_price_youpin_by_wear_bucket(self, template_id: int, wear: Decimal) -> Optional[float]:
        min_wear, max_wear = self.wear_bucket_2dp(wear)
        payload = self.query_raw("youpin", str(template_id), min_wear, max_wear)
        if not payload.get("ok"):
            return None
        cheapest = payload.get("cheapest")
        if not cheapest:
            return None
        price = cheapest.get("price") if isinstance(cheapest, dict) else None
        if price is None:
            return None
        try:
            return float(price)
        except Exception:
            return None
