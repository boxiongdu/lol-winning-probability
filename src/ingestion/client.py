"""Small synchronous Riot client with bounded retries and safe error messages."""
import time
from urllib.parse import quote
import requests

REGIONS = {"americas", "europe", "asia", "sea"}


class RiotError(RuntimeError):
    """API failure carrying an HTTP status but never credentials or response bodies."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class RiotClient:
    def __init__(self, api_key, region="americas", interval=1.3, retries=4,
                 session=None, sleep=time.sleep, clock=time.monotonic):
        if not api_key or not api_key.strip():
            raise ValueError("Set RIOT_API_KEY in the environment or local .env")
        if region not in REGIONS or interval < 0 or retries < 0:
            raise ValueError("Invalid Riot region, request interval, or retry count")
        self.region = region
        self.session = session or requests.Session()
        self.session.headers.update({"X-Riot-Token": api_key})
        self.interval, self.retries = interval, retries
        self.sleep, self.clock, self.last_request = sleep, clock, None
        self.requests = self.retry_count = 0

    def get(self, path, params=None):
        """Retry transient errors; wait at least Retry-After after HTTP 429."""
        for attempt in range(self.retries + 1):
            if self.last_request is not None:
                self.sleep(max(0, self.interval - (self.clock() - self.last_request)))
            self.last_request = self.clock()
            self.requests += 1
            try:
                response = self.session.get(
                    f"https://{self.region}.api.riotgames.com{path}",
                    params=params, timeout=(10, 45), allow_redirects=False,
                )
            except (requests.Timeout, requests.ConnectionError):
                if attempt == self.retries:
                    raise RiotError("Riot request failed after network retries") from None
                self.retry_count += 1
                self.sleep(min(2 ** attempt, 30))
                continue
            status = response.status_code
            if status == 200:
                try:
                    return response.json()
                except ValueError:
                    raise RiotError("Riot returned invalid JSON", status) from None
            if status in (401, 403):
                raise RiotError("Riot authentication failed; check key expiry and region", status)
            if status != 429 and not 500 <= status < 600:
                raise RiotError(f"Riot request rejected (HTTP {status})", status)
            if attempt == self.retries:
                raise RiotError(f"Riot retry budget exhausted (HTTP {status})", status)
            delay = min(2 ** attempt, 30)
            if status == 429:
                try:
                    delay = max(delay, float(response.headers.get("Retry-After", 1)))
                except (TypeError, ValueError):
                    pass
            self.retry_count += 1
            self.sleep(delay)

    def resolve(self, game_name, tag_line):
        """Resolve a Riot ID into a PUUID on the configured regional host."""
        return self.get(f"/riot/account/v1/accounts/by-riot-id/{quote(game_name, safe='')}/{quote(tag_line, safe='')}")["puuid"]

    def match_ids(self, puuid, queue=420, limit=200, start_time=None, end_time=None):
        """Yield deduplicated IDs from paginated history; times are Unix seconds."""
        if limit < 1:
            raise ValueError("History limit must be positive")
        seen = set()
        for start in range(0, limit, 100):
            count = min(100, limit - start)
            params = {"start": start, "count": count, "queue": queue}
            if start_time is not None:
                params["startTime"] = start_time
            if end_time is not None:
                params["endTime"] = end_time
            page = self.get(f"/lol/match/v5/matches/by-puuid/{quote(puuid, safe='')}/ids", params)
            if not isinstance(page, list) or any(not isinstance(x, str) for x in page):
                raise RiotError("Invalid match history response")
            for match_id in page:
                if match_id not in seen:
                    seen.add(match_id)
                    yield match_id
            if len(page) < count:
                break

    def match(self, match_id):
        return self.get(f"/lol/match/v5/matches/{quote(match_id, safe='')}")

    def timeline(self, match_id):
        return self.get(f"/lol/match/v5/matches/{quote(match_id, safe='')}/timeline")
