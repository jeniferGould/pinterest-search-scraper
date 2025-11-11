import json
import logging
import random
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

import requests
from bs4 import BeautifulSoup

from .utils_time import parse_pinterest_timestamp

logger = logging.getLogger("pinterest_parser")

@dataclass
class PinterestScraperConfig:
    base_url: str = "https://www.pinterest.com/search/pins/"
    timeout: float = 10.0
    max_retries: int = 3
    backoff_factor: float = 0.5
    user_agent: str = "PinterestSearchScraper/1.0"

class PinterestSearchScraper:
    """
    Scrapes Pinterest search results and extracts pin metadata.

    The scraper uses a heuristic parser that looks for structured JSON
    embedded in the page as well as HTML fallbacks. It is designed to
    fail gracefully and return an empty list if Pinterest changes its
    internal structure.
    """

    def __init__(
        self,
        base_url: str = "https://www.pinterest.com/search/pins/",
        timeout: float = 10.0,
        max_retries: int = 3,
        backoff_factor: float = 0.5,
        user_agent: str = "PinterestSearchScraper/1.0",
    ) -> None:
        self.config = PinterestScraperConfig(
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            backoff_factor=backoff_factor,
            user_agent=user_agent,
        )
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": self.config.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
            }
        )

    def _build_search_url(
        self, query: str, content_filter: str = "all"
    ) -> str:
        params = {
            "q": query,
            "rs": "typed",
        }
        if content_filter == "videos":
            params["video"] = "1"
        # We keep query params explicit rather than using requests here,
        # because we'll pass them separately to session.get.
        return self.config.base_url, params

    def _request_with_retries(
        self, url: str, params: Dict[str, Any]
    ) -> Optional[requests.Response]:
        for attempt in range(1, self.config.max_retries + 1):
            try:
                resp = self.session.get(
                    url,
                    params=params,
                    timeout=self.config.timeout,
                )
                if resp.status_code >= 500:
                    raise requests.HTTPError(
                        f"Server error {resp.status_code}"
                    )
                return resp
            except Exception as exc:
                wait = self.config.backoff_factor * (2 ** (attempt - 1))
                jitter = random.uniform(0, wait / 2)
                sleep_for = wait + jitter
                logger.warning(
                    "Request attempt %d failed (%s). Retrying in %.2fs...",
                    attempt,
                    exc,
                    sleep_for,
                )
                time.sleep(sleep_for)
        logger.error("All retry attempts failed for URL %s", url)
        return None

    def _extract_json_blobs_from_html(
        self, html: str
    ) -> List[Dict[str, Any]]:
        """
        Search the HTML for JSON blobs that might contain pin data.
        Pinterest often embeds a large JSON object in a script tag.
        """
        soup = BeautifulSoup(html, "html.parser")
        blobs: List[Dict[str, Any]] = []

        # Look for inline scripts that contain JSON-like structures
        for script in soup.find_all("script"):
            text = script.string or script.text
            if not text:
                continue

            # Common patterns seen in Pinterest pages
            if "__PWS_DATA__" in text or "initialReduxState" in text:
                try:
                    first_brace = text.index("{")
                    last_brace = text.rindex("}")
                    json_str = text[first_brace : last_brace + 1]
                    data = json.loads(json_str)
                    blobs.append(data)
                except Exception:
                    continue

            # Also try scripts that look like pure JSON
            if text.strip().startswith("{") and text.strip().endswith("}"):
                try:
                    blobs.append(json.loads(text))
                except Exception:
                    continue

        logger.debug("Extracted %d JSON blobs from HTML", len(blobs))
        return blobs

    def _iter_dicts(self, obj: Any) -> Iterable[Dict[str, Any]]:
        if isinstance(obj, dict):
            yield obj
            for v in obj.values():
                yield from self._iter_dicts(v)
        elif isinstance(obj, list):
            for v in obj:
                yield from self._iter_dicts(v)

    def _find_pin_objects(self, blobs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Recursively scan blobs for dicts that look like pin objects.

        Heuristics:
        - Has an 'id' field.
        - Contains 'images' or 'grid_title' or 'title'.
        """
        pins: List[Dict[str, Any]] = []
        for blob in blobs:
            for node in self._iter_dicts(blob):
                if "id" in node and (
                    "images" in node or "grid_title" in node or "title" in node
                ):
                    pins.append(node)
        logger.debug("Heuristic parser found %d candidate pins", len(pins))
        return pins

    def _fallback_parse_from_html(self, html: str) -> List[Dict[str, Any]]:
        """
        Fallback parser that attempts to extract minimal pin info from HTML only.
        """
        soup = BeautifulSoup(html, "html.parser")
        results: List[Dict[str, Any]] = []

        for pin_div in soup.find_all("div"):
            # This is intentionally broad; we rely on data attributes
            # that often show up in Pinterest pin elements.
            data_test_id = pin_div.get("data-test-id", "")
            if "pin" not in data_test_id.lower():
                continue

            img = pin_div.find("img")
            if not img:
                continue

            image_url = img.get("src") or img.get("data-src") or ""
            alt = img.get("alt") or ""

            pin_id = pin_div.get("data-pin-id") or ""
            if not pin_id:
                # Try to synthesize a pseudo-ID from the image URL
                m = re.search(r"/(\d+)/", image_url)
                pin_id = m.group(1) if m else image_url[-32:]

            pin: Dict[str, Any] = {
                "id": str(pin_id),
                "title": alt,
                "pinner": {
                    "id": None,
                    "username": None,
                    "fullName": None,
                    "avatarURL": None,
                    "followers": None,
                },
                "date": parse_pinterest_timestamp(None),
                "type": "pin",
                "imageURL": image_url,
            }
            results.append(pin)

        logger.debug("Fallback HTML parser found %d pins", len(results))
        return results

    def _extract_primary_image_url(self, pin_obj: Dict[str, Any]) -> Optional[str]:
        images = pin_obj.get("images")
        if not isinstance(images, dict):
            return None

        # Prioritise 'orig', then other sizes
        if "orig" in images and isinstance(images["orig"], dict):
            return images["orig"].get("url")

        for size_data in images.values():
            if isinstance(size_data, dict) and "url" in size_data:
                return size_data["url"]

        return None

    def _normalize_pin(self, pin_obj: Dict[str, Any]) -> Dict[str, Any]:
        pin_id = str(pin_obj.get("id"))
        title = (
            pin_obj.get("grid_title")
            or pin_obj.get("title")
            or pin_obj.get("description")
            or ""
        )

        pinner = pin_obj.get("pinner") or pin_obj.get("owner") or {}
        if not isinstance(pinner, dict):
            pinner = {}

        pinner_id = pinner.get("id") or pinner.get("user_id")
        username = pinner.get("username") or pinner.get("urlname")
        full_name = pinner.get("full_name") or pinner.get("fullName")
        avatar_url = None
        if "image_small_url" in pinner:
            avatar_url = pinner.get("image_small_url")
        elif "image_medium_url" in pinner:
            avatar_url = pinner.get("image_medium_url")
        elif "image_xlarge_url" in pinner:
            avatar_url = pinner.get("image_xlarge_url")

        followers = pinner.get("follower_count") or pinner.get(
            "followers"
        )

        created_at = (
            pin_obj.get("created_at")
            or pin_obj.get("created_at_timestamp")
            or pin_obj.get("createdAt")
        )
        date_info = parse_pinterest_timestamp(created_at)

        image_url = self._extract_primary_image_url(pin_obj) or ""

        normalized = {
            "id": pin_id,
            "title": title,
            "pinner": {
                "id": pinner_id,
                "username": username,
                "fullName": full_name,
                "avatarURL": avatar_url,
                "followers": followers,
            },
            "date": date_info,
            "type": pin_obj.get("type") or "pin",
            "imageURL": image_url,
        }
        return normalized

    def search(
        self,
        query: str,
        limit: int = 50,
        content_filter: str = "all",
    ) -> List[Dict[str, Any]]:
        """
        Run a Pinterest search and return a list of normalized pin dictionaries.
        """
        url, params = self._build_search_url(query, content_filter)
        logger.info("Fetching Pinterest search results for query %r", query)
        resp = self._request_with_retries(url, params)

        if resp is None:
            logger.error("No response received from Pinterest.")
            return []

        if resp.status_code != 200:
            logger.error(
                "Unexpected Pinterest status code %s for query %r",
                resp.status_code,
                query,
            )
            return []

        html = resp.text
        blobs = self._extract_json_blobs_from_html(html)
        pins_raw: List[Dict[str, Any]] = []
        if blobs:
            pins_raw = self._find_pin_objects(blobs)

        if not pins_raw:
            logger.warning(
                "Structured pin data not found for query %r, "
                "falling back to HTML parsing.",
                query,
            )
            return self._fallback_parse_from_html(html)[:limit]

        normalized: List[Dict[str, Any]] = []
        for pin_obj in pins_raw:
            try:
                normalized_pin = self._normalize_pin(pin_obj)
                normalized.append(normalized_pin)
                if len(normalized) >= limit:
                    break
            except Exception as exc:
                logger.debug("Failed to normalize pin object: %s", exc)

        logger.info(
            "Parsed %d pins (limit %d) for query %r",
            len(normalized),
            limit,
            query,
        )
        return normalized