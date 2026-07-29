from __future__ import annotations

import json
import re
import sys
import urllib.request
from html.parser import HTMLParser
from pathlib import Path


BASE_URL = "https://tomvsaji.com"
DATA_DIR = Path(__file__).resolve().parent / "data"
PAGES = {
    "home": "/",
    "about": "/about",
    "writing": "/blogs",
}


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hidden_depth = 0
        self.text: list[str] = []
        self.next_data = ""
        self.in_next_data = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "script" and attributes.get("id") == "__NEXT_DATA__":
            self.in_next_data = True
            return
        if tag in {"script", "style", "svg", "noscript"}:
            self.hidden_depth += 1
        if not self.hidden_depth and tag in {"p", "h1", "h2", "h3", "h4", "li", "a"}:
            self.text.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self.in_next_data:
            self.in_next_data = False
            return
        if tag in {"script", "style", "svg", "noscript"} and self.hidden_depth:
            self.hidden_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.in_next_data:
            self.next_data += data
        elif not self.hidden_depth:
            cleaned = re.sub(r"\s+", " ", data).strip()
            if cleaned:
                self.text.append(cleaned)

    def visible_text(self) -> str:
        text = " ".join(self.text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\s*\n\s*", "\n", text)
        return text.strip()


def fetch(path: str) -> tuple[str, dict]:
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        headers={"User-Agent": "TomVsajiSiteChat/1.0"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        html = response.read().decode("utf-8", errors="replace")
    parser = PageParser()
    parser.feed(html)
    next_data = json.loads(parser.next_data) if parser.next_data else {}
    return parser.visible_text(), next_data


def write_document(path: Path, title: str, source: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = f"# {title}\n\nSource: {source}\n\n{body.strip()}\n"
    path.write_text(content, encoding="utf-8")


def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    home_next_data: dict = {}

    for name, route in PAGES.items():
        text, next_data = fetch(route)
        structured_text = re.sub(r"\n+", "\n\n", text)
        write_document(
            DATA_DIR / f"site-{name}.md",
            f"Tom Vellavoor Saji — {name.title()}",
            f"{BASE_URL}{route}",
            structured_text,
        )
        if name == "home":
            home_next_data = next_data

    posts = home_next_data.get("props", {}).get("pageProps", {}).get("posts", [])
    written = 0
    for post in posts:
        slug = str(post.get("slug", "")).strip()
        title = str(post.get("title", slug)).strip()
        content = str(post.get("content", "")).strip()
        if not slug or not content:
            continue
        metadata = "\n\n".join(
            part
            for part in [
                f"Published: {post.get('publishedAt', '')}".strip(),
                f"Reading time: {post.get('readingTime', '')}".strip(),
                str(post.get("description", "")).strip(),
            ]
            if part and not part.endswith(":")
        )
        write_document(
            DATA_DIR / "posts" / f"{slug}.md",
            title,
            f"{BASE_URL}/blogs/{slug}",
            f"{metadata}\n\n{content}",
        )
        written += 1

    print(f"Synced {len(PAGES)} site pages and {written} posts from {BASE_URL}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Site sync failed: {error}", file=sys.stderr)
        raise
