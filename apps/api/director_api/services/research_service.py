"""Research service using Tavily search and BeautifulSoup extraction."""

from __future__ import annotations

import re
import urllib.parse
from typing import Any

import httpx
import structlog
from bs4 import BeautifulSoup

from director_api.config import Settings

log = structlog.get_logger(__name__)

# Wikimedia requires a descriptive User-Agent with contact info (URL or email) or requests may get 403.
# See https://meta.wikimedia.org/wiki/User-Agent_policy
_WIKIMEDIA_USER_AGENT = (
    "DirectelyStudio/0.1 (+https://github.com/; research fallback; respects robots.txt)"
)

# Words that often appear capitalized in briefs but are useless as Wikipedia OpenSearch queries.
_WIKI_OPENSEARCH_SKIP_WORDS = frozenset(
    {
        "write",
        "about",
        "tell",
        "the",
        "this",
        "that",
        "your",
        "with",
        "from",
        "into",
        "when",
        "where",
        "what",
        "which",
        "while",
        "keep",
        "documentary",
        "narration",
        "biblical",
        "story",
        "tone",
        "respectful",
        "historical",
        "narrative",
        "debate",
        "distinguish",
        "automated",
        "pipeline",
        "test",
        "his",
        "her",
        "their",
        "people",
        "reading",
        "law",
        "days",
        "role",
    }
)


def sanitize_jsonb_text(s: str, max_len: int | None = None) -> str:
    """PostgreSQL jsonb rejects U+0000 in string values; strip NULs and C0 controls."""
    if not s:
        return ""
    out: list[str] = []
    for ch in s.replace("\x00", ""):
        o = ord(ch)
        if o in (9, 10, 13) or o >= 32:
            out.append(ch)
    text = "".join(out)
    if max_len is not None:
        text = text[:max_len]
    return text


def append_negative_hint_to_image_prompt(
    prompt: str,
    negative: str | None,
    *,
    max_len: int = 4000,
    max_negative: int = 800,
) -> str:
    """
    For image APIs without a separate negative field, append a short ``Avoid:`` clause under ``max_len``.
    """
    n = sanitize_jsonb_text((negative or "").strip(), max_negative)
    if not n:
        return sanitize_jsonb_text(str(prompt or ""), max_len)
    suffix = f"\n\nAvoid: {n}"
    if len(suffix) >= max_len:
        return sanitize_jsonb_text(str(prompt or ""), max_len)
    room = max_len - len(suffix)
    if room < 80:
        return sanitize_jsonb_text(str(prompt or ""), max_len)
    trimmed = sanitize_jsonb_text(str(prompt or ""), room).rstrip()
    return sanitize_jsonb_text(trimmed + suffix, max_len)


def _wikipedia_opensearch_queries(topic: str) -> list[str]:
    """Build short OpenSearch attempts: essay-style topics often return zero hits on the full string."""
    t = (topic or "").strip()
    if not t:
        return ["documentary"]
    seen: set[str] = set()
    out: list[str] = []

    def add(raw: str) -> None:
        q = raw.strip()
        if len(q) < 2:
            return
        q = q[:280]
        if q in seen:
            return
        seen.add(q)
        out.append(q)

    add(t[:280])
    for part in re.split(r"[:\n]+", t):
        p = part.strip()
        if len(p) >= 4:
            add(p[:280])
    for w in re.findall(r"\b[A-Z][a-z]{2,}\b", t):
        if w.lower() in _WIKI_OPENSEARCH_SKIP_WORDS:
            continue
        add(w)
    return out[:14]


def _wikipedia_opensearch_once(query: str, settings: Settings, limit: int) -> list[dict[str, Any]]:
    lim = max(1, min(int(limit), 10))
    encoded = urllib.parse.quote(query)
    api_url = (
        "https://en.wikipedia.org/w/api.php"
        f"?action=opensearch&search={encoded}&limit={lim}&namespace=0&format=json"
    )
    headers = {
        "User-Agent": _WIKIMEDIA_USER_AGENT,
        "Accept": "application/json",
    }
    with httpx.Client(
        timeout=float(settings.research_http_timeout_sec),
        headers=headers,
        follow_redirects=True,
    ) as client:
        r = client.get(api_url)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list) or len(data) < 4:
        return []
    titles = data[1] if isinstance(data[1], list) else []
    descs = data[2] if isinstance(data[2], list) else []
    urls = data[3] if isinstance(data[3], list) else []
    out: list[dict[str, Any]] = []
    for i, url in enumerate(urls):
        if not isinstance(url, str) or not url.strip():
            continue
        title = titles[i] if i < len(titles) and isinstance(titles[i], str) else "Wikipedia"
        desc = descs[i] if i < len(descs) and isinstance(descs[i], str) else ""
        out.append(
            {
                "url": url.strip(),
                "title": sanitize_jsonb_text(title, 500) or "Wikipedia",
                "snippet": sanitize_jsonb_text(desc, 4000),
                "score": max(0.35, 0.9 - i * 0.06),
            }
        )
    return out


def _wikipedia_opensearch_hits(topic: str, settings: Settings, limit: int) -> list[dict[str, Any]]:
    """Wikipedia OpenSearch (no API key) — for local/LM-Studio-only setups without Tavily."""
    lim = max(1, min(int(limit), 10))
    merged: list[dict[str, Any]] = []
    seen_url: set[str] = set()
    for q in _wikipedia_opensearch_queries(topic):
        batch = _wikipedia_opensearch_once(q, settings, lim)
        for h in batch:
            u = h.get("url") or ""
            if not u or u in seen_url:
                continue
            seen_url.add(u)
            merged.append(h)
            if len(merged) >= lim:
                return merged[:lim]
    return merged


def search_web(topic: str, settings: Settings, limit: int) -> list[dict[str, Any]]:
    """Return normalized web search hits: Tavily when ``TAVILY_API_KEY`` is set, else Wikipedia OpenSearch."""
    key = (settings.tavily_api_key or "").strip()
    if key:
        try:
            from tavily import TavilyClient

            client = TavilyClient(api_key=key)
            res = client.search(
                query=topic,
                max_results=max(1, min(limit, 10)),
                include_answer=True,
                search_depth="advanced",
            )
            out: list[dict[str, Any]] = []
            for item in (res.get("results") or []):
                out.append(
                    {
                        "url": str(item.get("url") or ""),
                        "title": sanitize_jsonb_text(
                            str(item.get("title") or item.get("url") or "Untitled"), 500
                        ),
                        "snippet": sanitize_jsonb_text(str(item.get("content") or ""), 4000),
                        "score": item.get("score"),
                    }
                )
            hits = [x for x in out if x["url"]]
            if hits:
                return hits
            log.warning("tavily_empty_results_falling_back_wikipedia", topic=topic[:120])
        except ValueError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("tavily_failed_falling_back_wikipedia", error=str(e)[:300])
    wiki = _wikipedia_opensearch_hits(topic, settings, limit)
    if wiki:
        if not key:
            log.info("research_using_wikipedia_opensearch", topic=topic[:120], n=len(wiki))
        return wiki
    raise ValueError(
        "RESEARCH_NO_RESULTS: No web URLs found for this topic (Tavily empty or missing; Wikipedia OpenSearch returned nothing). "
        "Broaden or rephrase the project topic and retry."
    )


def extract_page_summary(url: str, settings: Settings) -> str:
    """Fetch URL and extract readable paragraph text."""
    try:
        with httpx.Client(
            timeout=settings.research_http_timeout_sec,
            follow_redirects=True,
            headers={"User-Agent": _WIKIMEDIA_USER_AGENT},
        ) as client:
            r = client.get(url)
        if r.status_code >= 400:
            return ""
        data = r.content or b""
        if data[:4] == b"%PDF" or data[:5] == b"%PDF-":
            return ""
        ct = (r.headers.get("content-type") or "").lower()
        if "application/pdf" in ct:
            return ""
        raw = r.content.decode("utf-8", errors="replace")
        head = raw.lstrip()[:8000].lower()
        looks_html = (
            "text/html" in ct
            or "application/xhtml" in ct
            or head.startswith("<!doctype html")
            or head.startswith("<html")
        )
        if "text/plain" in ct and not looks_html:
            return sanitize_jsonb_text(raw, settings.research_extract_chars)
        if not looks_html:
            return ""
        soup = BeautifulSoup(raw, "html.parser")
        for bad in soup(["script", "style", "noscript"]):
            bad.extract()
        paras = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
        text = " ".join(x for x in paras if x)
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            text = soup.get_text(" ", strip=True)
            text = re.sub(r"\s+", " ", text).strip()
        return sanitize_jsonb_text(text, settings.research_extract_chars)
    except Exception:
        return ""
