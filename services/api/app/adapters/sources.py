"""External source adapters for monitored public inputs.

The adapters keep the first MVP boundary deliberately small: fetch only from
registered HTTP(S) locations, return compact snapshots, and make redirect/host
validation explicit so callers can store the result without keeping full pages.
"""

from __future__ import annotations

import hashlib
import html
import ipaddress
import re
import socket
from xml.etree import ElementTree as ET
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import urlparse, urlunparse
from email.utils import parsedate_to_datetime
import math

import httpx


MAX_REDIRECTS = 5
MAX_BODY_BYTES = 2_000_000
MAX_CONTENT_CHARS = 8_000
REQUEST_TIMEOUT_SECONDS = 10.0
USER_AGENT = "replan-source-adapter/0.1"
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"


def fetch_weather(site: dict[str, Any], days: int = 7, *, transport: httpx.BaseTransport | None = None) -> dict[str, Any]:
    """Fetch a compact Open-Meteo forecast snapshot for an approved site."""

    fetched_at = _utcnow()
    source_id = str(site.get("source_id") or site.get("weather_site_id") or site.get("id") or "weather")
    try:
        latitude = _required_float(site, "latitude", "lat")
        longitude = _required_float(site, "longitude", "lon", "lng")
        if not math.isfinite(latitude) or not math.isfinite(longitude) or not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise ValueError("invalid coordinates")
        forecast_days = max(1, min(int(days), 16))
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "forecast_days": forecast_days,
            "timezone": site.get("timezone", "UTC"),
            "daily": ",".join(
                [
                    "temperature_2m_max",
                    "temperature_2m_min",
                    "precipitation_sum",
                    "wind_speed_10m_max",
                ]
            ),
        }
        with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS, transport=transport, headers={"User-Agent": USER_AGENT}) as client:
            response = client.get(OPEN_METEO_URL, params=params)
            response.raise_for_status()
        payload = response.json()
        daily = payload.get("daily") if isinstance(payload, dict) else None
        units = payload.get("daily_units", {}) if isinstance(payload, dict) else {}
        if not isinstance(daily, dict) or "time" not in daily:
            return _error("invalid_response", source_id, fetched_at, "weather response did not include daily forecast")
        data = _daily_rows(daily)
        return {
            "status": "ok",
            "source_id": source_id,
            "fetched_at": fetched_at,
            "provider": "open_meteo",
            "url": str(response.url),
            "site": site,
            "forecast": {
                "validity": {
                    "start": data[0]["date"] if data else None,
                    "end": data[-1]["date"] if data else None,
                    "days": len(data),
                },
                "units": units,
                "data": data,
            },
            "body_hash": _hash_text(response.text),
        }
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        return _error("error", source_id, fetched_at, str(exc))


def fetch_seasonal_statistics(site: dict[str, Any], limits: dict[str, float], *, transport: httpx.BaseTransport | None = None) -> dict[str, Any]:
    """Historical monthly exceedance frequencies; never a forecast or confirmed outage."""
    fetched_at = _utcnow()
    source_id = f"{site.get('source_id') or site.get('weather_site_id') or site.get('id') or 'weather'}:seasonal"
    try:
        latitude = _required_float(site, "latitude", "lat")
        longitude = _required_float(site, "longitude", "lon", "lng")
        if not math.isfinite(latitude) or not math.isfinite(longitude) or not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise ValueError("invalid coordinates")
        last_year = datetime.now(timezone.utc).year - 1
        params = {"latitude": latitude, "longitude": longitude,
                  "start_date": f"{last_year - 4}-01-01", "end_date": f"{last_year}-12-31",
                  "timezone": site.get("timezone", "UTC"),
                  "daily": "precipitation_sum,wind_speed_10m_max"}
        with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS, transport=transport, headers={"User-Agent": USER_AGENT}) as client:
            response = client.get(OPEN_METEO_ARCHIVE_URL, params=params)
            response.raise_for_status()
        payload = response.json()
        daily = payload.get("daily") if isinstance(payload, dict) else None
        units = payload.get("daily_units", {}) if isinstance(payload, dict) else {}
        if (not isinstance(daily, dict) or not isinstance(daily.get("time"), list)
                or units.get("precipitation_sum") != "mm" or units.get("wind_speed_10m_max") != "km/h"):
            raise ValueError("historical daily data or units are missing")
        months: dict[str, dict[str, Any]] = {}
        for index, raw_day in enumerate(daily["time"]):
            month = str(date.fromisoformat(raw_day).month)
            row = months.setdefault(month, {"observed_days": 0, "precipitation_exceedance_days": 0,
                                            "wind_exceedance_days": 0})
            precipitation = daily["precipitation_sum"][index]
            wind = daily["wind_speed_10m_max"][index]
            if precipitation is None or wind is None:
                continue
            row["observed_days"] += 1
            if "max_precipitation_mm" in limits and float(precipitation) > limits["max_precipitation_mm"]:
                row["precipitation_exceedance_days"] += 1
            if "max_wind_speed_kmh" in limits and float(wind) > limits["max_wind_speed_kmh"]:
                row["wind_exceedance_days"] += 1
        if not months:
            raise ValueError("historical daily data is empty")
        return {"status": "ok", "source_id": source_id, "provider": "open_meteo_archive",
                "url": str(response.url), "fetched_at": fetched_at, "body_hash": _hash_text(response.text),
                "site": site, "seasonal_statistics": {"period_start": params["start_date"],
                                                "period_end": params["end_date"], "limits": limits,
                                                "by_month": months}}
    except (httpx.HTTPError, ValueError, TypeError, KeyError, IndexError) as exc:
        return _error("error", source_id, fetched_at, str(exc))


def fetch_registered_source(
    url: str,
    allowed_hosts: list[str],
    *,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    """Fetch a registered public source with allowlist and SSRF checks."""

    fetched_at = _utcnow()
    source_id = _source_id(url)
    try:
        current_url = _canonical_url(url)
        _assert_allowed_url(current_url, allowed_hosts)
        with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS, transport=transport, headers={"User-Agent": USER_AGENT}) as client:
            response = _get_with_checked_redirects(client, current_url, allowed_hosts)
        content_type = response.headers.get("content-type", "")
        raw = response.content[:MAX_BODY_BYTES]
        text = _decode_response_text(raw, response.encoding)
        feed = _extract_feed(text)
        title = feed.get("title") or _extract_title(text) or _canonical_url(str(response.url))
        content = feed.get("content") or _extract_visible_text(text, content_type)
        summary = feed.get("summary") or _summarize(content)
        return {
            "status": "ok",
            "source_id": _source_id(str(response.url)),
            "fetched_at": fetched_at,
            "url": _canonical_url(str(response.url)),
            "title": title,
            "summary": summary,
            "content": content[:MAX_CONTENT_CHARS],
            "body_hash": _hash_text(_normalize_text(content)),
            "content_type": content_type,
            "feed_items": feed.get("items", []),
        }
    except (httpx.HTTPError, ValueError, UnicodeDecodeError) as exc:
        return _error("error", source_id, fetched_at, str(exc), url=url)


def _get_with_checked_redirects(client: httpx.Client, url: str, allowed_hosts: list[str]) -> httpx.Response:
    current_url = url
    for _ in range(MAX_REDIRECTS + 1):
        response = client.get(current_url, follow_redirects=False)
        if not response.is_redirect:
            response.raise_for_status()
            return response
        location = response.headers.get("location")
        if not location:
            raise ValueError("redirect response did not include Location")
        next_url = _canonical_url(str(response.url.join(location)))
        _assert_allowed_url(next_url, allowed_hosts)
        current_url = next_url
    raise ValueError("too many redirects")


def _assert_allowed_url(url: str, allowed_hosts: list[str]) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("only http and https sources are supported")
    if not parsed.hostname:
        raise ValueError("source URL must include a hostname")
    host = parsed.hostname.lower().rstrip(".")
    if not _host_allowed(host, allowed_hosts):
        raise ValueError("source host is not in the allowlist")
    _assert_public_host(host)


def _host_allowed(host: str, allowed_hosts: list[str]) -> bool:
    normalized = [item.lower().strip().rstrip(".") for item in allowed_hosts if item.strip()]
    for allowed in normalized:
        if allowed.startswith("*.") and host.endswith(allowed[1:]) and host != allowed[2:]:
            return True
        if allowed.startswith(".") and host.endswith(allowed) and host != allowed[1:]:
            return True
        if host == allowed:
            return True
    return False


def _assert_public_host(host: str) -> None:
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)}
    except socket.gaierror as exc:
        raise ValueError(f"could not resolve source host: {host}") from exc
    if not addresses:
        raise ValueError("source host did not resolve")
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise ValueError("source host resolved to a non-public network address")


def _canonical_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("source URL must be absolute")
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if not hostname:
        raise ValueError("source URL must include a hostname")
    port = parsed.port
    netloc = hostname
    if port and not ((parsed.scheme == "http" and port == 80) or (parsed.scheme == "https" and port == 443)):
        netloc = f"{hostname}:{port}"
    path = parsed.path or "/"
    return urlunparse((parsed.scheme.lower(), netloc, path, "", parsed.query, ""))


def _source_id(url: str) -> str:
    try:
        canonical = _canonical_url(url)
    except ValueError:
        canonical = url
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _required_float(site: dict[str, Any], *keys: str) -> float:
    for key in keys:
        if key in site and site[key] not in (None, ""):
            return float(site[key])
    raise ValueError(f"site must include one of: {', '.join(keys)}")


def _daily_rows(daily: dict[str, Any]) -> list[dict[str, Any]]:
    times = daily.get("time", [])
    if not isinstance(times, list):
        return []
    rows: list[dict[str, Any]] = []
    for index, day in enumerate(times):
        row = {"date": day}
        for key, values in daily.items():
            if key == "time":
                continue
            if isinstance(values, list) and index < len(values):
                row[key] = values[index]
        rows.append(row)
    return rows


def _decode_response_text(raw: bytes, encoding: str | None) -> str:
    return raw.decode(encoding or "utf-8", errors="replace")


def _extract_feed(text: str) -> dict[str, Any]:
    """Read RSS/Atom metadata when a registered public source is a feed."""
    stripped = text.lstrip()
    if not stripped.startswith("<") or not any(marker in stripped[:500].lower() for marker in ("<rss", "<feed", "<rdf:rdf")):
        return {}
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return {}
    def value(node: ET.Element | None, names: tuple[str, ...]) -> str:
        if node is None:
            return ""
        for child in list(node):
            if child.tag.rsplit("}", 1)[-1].lower() in names and (child.text or "").strip():
                return (child.text or "").strip()
        return ""
    items: list[dict[str, str]] = []
    for node in list(root.iter()):
        if node.tag.rsplit("}", 1)[-1].lower() not in {"item", "entry"}:
            continue
        item = {"title": value(node, ("title",)), "summary": value(node, ("description", "summary", "content")), "published_at": value(node, ("pubdate", "published", "updated"))}
        if item["title"] or item["summary"]:
            item["summary"] = _extract_visible_text(item["summary"], "text/html")
            raw_date = item["published_at"]
            try:
                item["published_at"] = parsedate_to_datetime(raw_date).isoformat()
            except (ValueError, TypeError):
                try:
                    item["published_at"] = datetime.fromisoformat(raw_date.replace("Z", "+00:00")).isoformat()
                except ValueError:
                    item["published_at"] = ""
            item["id"] = value(node, ("guid", "id"))
            item["url"] = value(node, ("link",))
            if not item["url"]:
                link = next((child for child in node if child.tag.rsplit("}", 1)[-1] == "link" and child.get("rel", "alternate") == "alternate"), None)
                if link is not None:
                    item["url"] = link.get("href", "")
            if urlparse(item["url"]).scheme not in {"http", "https"}:
                item["url"] = ""
            items.append(item)
        if len(items) >= 20:
            break
    channel = next((node for node in root.iter() if node.tag.rsplit("}", 1)[-1].lower() in {"channel", "feed"}), root)
    feed_title = value(channel, ("title",))
    latest = items[0] if items else {}
    return {"title": feed_title or latest.get("title", ""), "summary": latest.get("summary", ""), "content": "\n".join(f"{item['title']}: {item['summary']}" for item in items), "items": items}


def _extract_title(text: str) -> str | None:
    match = re.search(r"<title[^>]*>(.*?)</title>", text, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    return _normalize_text(html.unescape(re.sub(r"<[^>]+>", " ", match.group(1))))[:300] or None


def _extract_visible_text(text: str, content_type: str) -> str:
    if "html" not in content_type.lower():
        return _normalize_text(text)
    stripped = re.sub(r"(?is)<(script|style|noscript|svg|template)[^>]*>.*?</\1>", " ", text)
    stripped = re.sub(r"(?s)<!--.*?-->", " ", stripped)
    stripped = re.sub(r"(?s)<[^>]+>", " ", stripped)
    return _normalize_text(html.unescape(stripped))


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _summarize(content: str) -> str:
    if len(content) <= 500:
        return content
    return content[:497].rsplit(" ", 1)[0].rstrip() + "..."


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _error(status: str, source_id: str, fetched_at: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"status": status, "source_id": source_id, "fetched_at": fetched_at, "error": message, **extra}


def fetch_holidays(country_code: str, year: int, *, transport: httpx.BaseTransport | None = None) -> dict[str, Any]:
    """Nager.Date public calendar data; applicability to working days needs review."""
    fetched_at = _utcnow()
    source_id = f"nager:{country_code}:{year}"
    if not re.fullmatch(r"[A-Z]{2}", country_code) or not 2000 <= year <= 2100:
        return _error("error", source_id, fetched_at, "invalid country/year")
    url = f"https://date.nager.at/api/v3/PublicHolidays/{year}/{country_code}"
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS, transport=transport, headers={"User-Agent": USER_AGENT}) as client:
            response = client.get(url)
            response.raise_for_status()
        rows = response.json()
        if not isinstance(rows, list) or any(not isinstance(row, dict) or not row.get("date") for row in rows):
            raise ValueError("invalid holiday response")
        return {"status": "ok", "source_id": source_id, "provider": "nager_date",
                "url": url, "fetched_at": fetched_at, "holidays": rows,
                "body_hash": _hash_text(response.text)}
    except (httpx.HTTPError, ValueError) as exc:
        return _error("error", source_id, fetched_at, str(exc), url=url)
