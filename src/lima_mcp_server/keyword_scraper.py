from __future__ import annotations

import argparse
import re
from collections import Counter
from html.parser import HTMLParser

import requests

try:
    from bs4 import BeautifulSoup
except ModuleNotFoundError:  # pragma: no cover - exercised in envs without bs4
    BeautifulSoup = None

STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "has",
    "he",
    "in",
    "is",
    "it",
    "its",
    "of",
    "on",
    "that",
    "the",
    "to",
    "was",
    "were",
    "will",
    "with",
}

WORD_PATTERN = re.compile(r"[a-zA-Z][a-zA-Z'-]*")


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip_stack: list[str] = []
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._skip_stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if self._skip_stack and self._skip_stack[-1] == tag:
            self._skip_stack.pop()

    def handle_data(self, data: str) -> None:
        if self._skip_stack:
            return
        cleaned = data.strip()
        if cleaned:
            self.parts.append(cleaned)


def _html_to_text(html: str) -> str:
    if BeautifulSoup is not None:
        soup = BeautifulSoup(html, "html.parser")
        for element in soup(["script", "style", "noscript"]):
            element.decompose()
        return soup.get_text(separator=" ")

    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    return " ".join(parser.parts)


def extract_top_keywords_from_html(html: str, top_n: int = 10) -> list[tuple[str, int]]:
    """Extract the top N keywords from HTML text content."""
    text = _html_to_text(html)
    words = (word.lower() for word in WORD_PATTERN.findall(text))
    filtered_words = [word for word in words if len(word) > 2 and word not in STOPWORDS]
    counts = Counter(filtered_words)
    return counts.most_common(top_n)


def extract_top_keywords_from_url(url: str, top_n: int = 10, timeout: int = 10) -> list[tuple[str, int]]:
    """Fetch a URL and return top N keywords."""
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    return extract_top_keywords_from_html(response.text, top_n=top_n)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scrape and list top website keywords.")
    parser.add_argument("url", help="Website URL to analyze (for example: https://example.com)")
    parser.add_argument("--top", type=int, default=10, help="Number of keywords to return (default: 10)")
    args = parser.parse_args(argv)

    try:
        keywords = extract_top_keywords_from_url(args.url, top_n=args.top)
    except requests.RequestException as exc:
        parser.exit(1, f"Failed to fetch URL: {exc}\n")

    if not keywords:
        print(f"No keywords found for {args.url}.")
        return 0

    print(f"Top {len(keywords)} keywords for {args.url}:")
    for word, count in keywords:
        print(f"{word}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
