#!/usr/bin/env python3
"""Extract a compact, reviewable inventory from the static HTML prototype."""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROTOTYPE_DIR = ROOT / "ui-prototype"


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


class PrototypeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, dict[str, str], list[str]]] = []
        self.title = ""
        self.body_page = ""
        self.headings: list[dict[str, str]] = []
        self.labels: list[dict[str, str]] = []
        self.controls: list[dict[str, str]] = []
        self.buttons: list[dict[str, str]] = []
        self.table_headers: list[list[str]] = []
        self.links: list[dict[str, str]] = []
        self.dialogs: list[dict[str, str]] = []
        self._current_table_headers: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key: value or "" for key, value in attrs}
        self.stack.append((tag, attr_map, []))
        if tag == "body":
            self.body_page = attr_map.get("data-page", "")
        if tag in {"input", "select", "textarea"}:
            self.controls.append(
                {
                    "tag": tag,
                    "id": attr_map.get("id", ""),
                    "type": attr_map.get("type", ""),
                    "name": attr_map.get("name", ""),
                    "placeholder": attr_map.get("placeholder", ""),
                    "aria_label": attr_map.get("aria-label", ""),
                    "required": "required" if "required" in attr_map else "",
                }
            )
        if tag == "table":
            self._current_table_headers = []
        if tag in {"section", "aside", "div"} and attr_map.get("role") in {"dialog", "alertdialog"}:
            self.dialogs.append(
                {
                    "id": attr_map.get("id", ""),
                    "role": attr_map.get("role", ""),
                    "labelledby": attr_map.get("aria-labelledby", ""),
                }
            )

    def handle_data(self, data: str) -> None:
        if self.stack:
            self.stack[-1][2].append(data)

    def handle_endtag(self, tag: str) -> None:
        if not self.stack:
            return
        open_tag, attrs, chunks = self.stack.pop()
        if open_tag != tag:
            return
        text = clean_text("".join(chunks))
        if self.stack and text:
            self.stack[-1][2].append(text)
        if tag == "title":
            self.title = text
        elif tag in {"h1", "h2", "h3"} and text:
            self.headings.append({"level": tag, "id": attrs.get("id", ""), "text": text})
        elif tag == "label" and text:
            self.labels.append({"for": attrs.get("for", ""), "text": text})
        elif tag == "button" and text:
            self.buttons.append(
                {
                    "id": attrs.get("id", ""),
                    "type": attrs.get("type", ""),
                    "text": text,
                    "data_action": attrs.get("data-action", ""),
                    "data_panel": attrs.get("data-panel", ""),
                }
            )
        elif tag == "a" and text:
            self.links.append({"href": attrs.get("href", ""), "text": text})
        elif tag == "th" and text and self._current_table_headers is not None:
            self._current_table_headers.append(text)
        elif tag == "table" and self._current_table_headers is not None:
            if self._current_table_headers:
                self.table_headers.append(self._current_table_headers)
            self._current_table_headers = None


def page_inventory(path: Path) -> dict[str, object]:
    parser = PrototypeParser()
    parser.feed(path.read_text(encoding="utf-8"))
    return {
        "file": path.name,
        "title": parser.title,
        "page": parser.body_page,
        "headings": parser.headings,
        "labels": parser.labels,
        "controls": parser.controls,
        "buttons": parser.buttons,
        "table_headers": parser.table_headers,
        "links": parser.links,
        "dialogs": parser.dialogs,
    }


def main() -> None:
    pages = [page_inventory(path) for path in sorted(PROTOTYPE_DIR.glob("[0-9][0-9]-*.html"))]
    print(json.dumps(pages, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
