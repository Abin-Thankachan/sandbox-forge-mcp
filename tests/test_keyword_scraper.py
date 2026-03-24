from __future__ import annotations

import pytest
import requests

from lima_mcp_server.keyword_scraper import extract_top_keywords_from_html, extract_top_keywords_from_url


def test_extract_top_keywords_from_html_filters_noise() -> None:
    html = """
    <html>
      <head>
        <title>Python Data Data</title>
        <style>.hidden { color: red; }</style>
        <script>var token = "ignore-me";</script>
      </head>
      <body>
        <h1>Python scraping with BeautifulSoup</h1>
        <p>Python parser parser parser website keyword extraction.</p>
      </body>
    </html>
    """

    result = extract_top_keywords_from_html(html, top_n=4)

    assert result[0] == ("python", 3)
    assert result[1] == ("parser", 3)
    words = {word for word, _ in result}
    assert "token" not in words
    assert "hidden" not in words


def test_extract_top_keywords_from_html_uses_top_n_limit() -> None:
    html = "<p>alpha alpha beta gamma delta epsilon zeta</p>"
    result = extract_top_keywords_from_html(html, top_n=3)
    assert len(result) == 3


def test_extract_top_keywords_from_url_fetches_and_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    html = "<html><body>cloud cloud edge compute</body></html>"

    class DummyResponse:
        text = html

        def raise_for_status(self) -> None:
            return None

    def fake_get(url: str, timeout: int) -> DummyResponse:
        assert url == "https://example.com"
        assert timeout == 5
        return DummyResponse()

    monkeypatch.setattr(requests, "get", fake_get)

    result = extract_top_keywords_from_url("https://example.com", top_n=2, timeout=5)
    assert result == [("cloud", 2), ("edge", 1)]
