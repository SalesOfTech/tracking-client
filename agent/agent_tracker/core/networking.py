from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests
from requests import Response, Session


LOG = logging.getLogger(__name__)


@dataclass
class HttpClient:
    base_url: str
    timeout: int = 10
    session: Optional[Session] = None

    def __post_init__(self) -> None:
        if self.session is None:
            self.session = requests.Session()
            self.session.headers.update({"User-Agent": "SOFT.AgentTracker/0.1"})

    def post_json(
        self,
        path: str,
        payload: Dict[str, Any],
        retries: int = 1,
        expect_json: bool = True,
    ) -> Dict[str, Any]:
        url = self._build_url(path)
        backoff = 1.5
        for attempt in range(1, retries + 1):
            try:
                LOG.debug("POST %s (attempt %s)", url, attempt)
                resp = self.session.post(url, json=payload, timeout=self.timeout, allow_redirects=False)
                if resp.status_code != 200:
                    resp.raise_for_status()
                    raise requests.RequestException("Unexpected response status")
                resp.raise_for_status()
                if expect_json:
                    return self._parse_json(resp)
                return {}
            except requests.RequestException as exc:
                LOG.warning("Request to %s failed: %s", url, exc)
                if attempt == retries:
                    raise
                sleep_for = backoff ** attempt
                time.sleep(sleep_for)
        return {}

    def _build_url(self, path: str) -> str:
        if path.startswith("http"):
            return path
        return f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"

    @staticmethod
    def _parse_json(resp: Response) -> Dict[str, Any]:
        try:
            return resp.json()
        except json.JSONDecodeError as exc:
            LOG.error("Cannot decode JSON response: %s", exc)
            raise
