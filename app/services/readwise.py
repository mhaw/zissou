import json
import logging
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


class ReadwiseImportError(Exception):
    """Raised when a Readwise shared view cannot be processed."""


@dataclass
class ReadwiseArticle:
    title: str
    url: str
    source: Optional[str] = None
    author: Optional[str] = None


def fetch_shared_view(
    shared_url: str, *, session: Optional[requests.Session] = None
) -> dict:
    if not shared_url or not shared_url.startswith("http"):
        raise ReadwiseImportError("Please provide a valid Readwise shared link.")

    client = session or requests.Session()
    try:
        response = client.get(shared_url, timeout=10)
    except requests.RequestException as exc:
        raise ReadwiseImportError(
            "Failed to load Readwise shared view. Please try again."
        ) from exc

    if response.status_code >= 400:
        raise ReadwiseImportError(
            f"Readwise shared view returned HTTP {response.status_code}."
        )

    articles = _parse_shared_html(response.text, shared_url)
    if not articles:
        raise ReadwiseImportError("No articles were found in the shared view.")

    soup = BeautifulSoup(response.text, "html.parser")
    title = (
        soup.title.string.strip()
        if soup.title and soup.title.string
        else "Readwise Shared View"
    )

    return {"title": title, "articles": articles}


def _parse_shared_html(html: str, base_url: str) -> List[ReadwiseArticle]:
    soup = BeautifulSoup(html, "html.parser")
    from_json = _extract_from_next_data(soup)
    if from_json:
        return from_json
    return _extract_from_dom(soup, base_url)


def _extract_from_next_data(soup: BeautifulSoup) -> List[ReadwiseArticle]:
    script = soup.find("script", id="__NEXT_DATA__")
    if not script or not script.string:
        return []

    try:
        payload = json.loads(script.string)
    except json.JSONDecodeError:
        logger.debug("Failed to parse __NEXT_DATA__ JSON from Readwise shared view")
        return []

    collected: list[ReadwiseArticle] = []

    def walk(node):
        if isinstance(node, dict):
            if _looks_like_article(node):
                url = node.get("source_url") or node.get("url")
                title = (
                    node.get("title")
                    or node.get("document_title")
                    or node.get("headline")
                )
                if url and title:
                    article = ReadwiseArticle(
                        title=title.strip(),
                        url=url.strip(),
                        source=(
                            node.get("site")
                            or node.get("source")
                            or node.get("publication")
                        ),
                        author=(node.get("author") or node.get("byline")),
                    )
                    collected.append(article)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(payload)

    unique: dict[str, ReadwiseArticle] = {}
    for article in collected:
        if article.url not in unique:
            unique[article.url] = article
    return list(unique.values())


def _looks_like_article(node: dict) -> bool:
    if "title" not in node and "document_title" not in node and "headline" not in node:
        return False
    if "source_url" not in node and "url" not in node:
        return False
    return True


def _extract_from_dom(soup: BeautifulSoup, base_url: str) -> List[ReadwiseArticle]:
    articles: list[ReadwiseArticle] = []
    seen: set[str] = set()
    selectors = [
        "[data-reading-item-id] a[href]",
        "a[data-reading-item-id]",
        "a[data-item-url]",
        "a[href][data-reading-item-url]",
    ]
    links: list = []
    for selector in selectors:
        links.extend(soup.select(selector))

    candidate_links = list(links) + list(soup.find_all("a"))

    for link in candidate_links:
        href = link.get("data-reading-item-url") or link.get("href")
        if not href:
            continue
        absolute = urljoin(base_url, href)
        if absolute in seen:
            continue
        text = link.get_text(strip=True)
        if not text:
            continue
        articles.append(ReadwiseArticle(title=text, url=absolute))
        seen.add(absolute)
    return articles
