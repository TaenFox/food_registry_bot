from html.parser import HTMLParser
from pathlib import Path


def test_placeholder_has_no_links_or_external_resources() -> None:
    tags = set()
    resource_attributes = []

    class PlaceholderParser(HTMLParser):
        def handle_starttag(self, tag, attrs):
            tags.add(tag)
            resource_attributes.extend(
                name for name, _ in attrs if name in {"href", "src", "action"}
            )

    page = Path(__file__).resolve().parents[1] / "mini-app" / "index.html"
    html = page.read_text(encoding="utf-8")
    parser = PlaceholderParser()
    parser.feed(html)

    assert "<h1>Дневник питания</h1>" in html
    assert {"a", "script", "button", "form"}.isdisjoint(tags)
    assert resource_attributes == []
