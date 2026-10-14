#!/usr/bin/env python3
"""competitor-watch — snapshot public pages and diff them over time.

Standard library only. Point it at public, no-login sources (GitHub releases,
RSS/Atom, changelog/pricing pages, public JSON endpoints); it records a
baseline, then reports what changed since the last run.

Usage:
    python3 watch.py --config sources.json --state .cw-state --out brief.md

See SKILL.md for source types and guardrails.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from difflib import unified_diff
from html.parser import HTMLParser

USER_AGENT = "competitor-watch/0.1 (+public-source monitor; stdlib)"
REQUEST_DELAY = 1.0  # seconds between requests, be a polite citizen
TIMEOUT = 20


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
def http_get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


# --------------------------------------------------------------------------
# Text normalization / HTML extraction
# --------------------------------------------------------------------------
class _TextExtractor(HTMLParser):
    """Pull visible text out of an HTML document, dropping script/style."""

    SKIP = {"script", "style", "noscript", "template", "svg", "head"}

    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self._chunks: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if not self._skip_depth:
            self._chunks.append(data)

    def text(self) -> str:
        return " ".join(self._chunks)


def html_to_text(html: str) -> str:
    p = _TextExtractor()
    try:
        p.feed(html)
    except Exception:
        pass
    text = p.text() or re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text).strip()


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


# --------------------------------------------------------------------------
# Source fetchers -> (fingerprint, text_digest, items[])
# item = {"id", "title", "date", "url"}
# --------------------------------------------------------------------------
def fetch_github_releases(src: dict) -> tuple[str, str, list[dict]]:
    repo = src["repo"]
    url = f"https://api.github.com/repos/{repo}/releases?per_page=30"
    data = json.loads(http_get(url))
    items = []
    for rel in data:
        items.append({
            "id": rel.get("tag_name") or rel.get("id"),
            "title": rel.get("name") or rel.get("tag_name") or "(release)",
            "date": (rel.get("published_at") or "")[:10],
            "url": rel.get("html_url") or "",
        })
    digest = "\n".join(f"{i['id']} | {i['title']} | {i['date']}" for i in items)
    fp = sha(",".join(str(i["id"]) for i in items))
    return fp, digest, items


def fetch_rss(src: dict) -> tuple[str, str, list[dict]]:
    raw = http_get(src["url"])
    root = ET.fromstring(raw)
    items = []
    # RSS 2.0
    for it in root.iter("item"):
        title = _findtext(it, "title")
        link = _findtext(it, "link")
        guid = _findtext(it, "guid") or link or title
        date = _findtext(it, "pubDate") or _findtext(it, "date")
        items.append({"id": guid, "title": title, "date": _short_date(date), "url": link})
    # Atom
    if not items:
        ns = {"a": "http://www.w3.org/2005/Atom"}
        for it in root.findall("a:entry", ns) or root.iter("{http://www.w3.org/2005/Atom}entry"):
            title = _findtext(it, "title") or _findtext(it, "{http://www.w3.org/2005/Atom}title")
            link = ""
            for ln in it.findall("link") or it.findall("{http://www.w3.org/2005/Atom}link"):
                link = ln.get("href", "") or ln.text or link
            guid = _findtext(it, "id") or _findtext(it, "{http://www.w3.org/2005/Atom}id") or link or title
            date = _findtext(it, "updated") or _findtext(it, "{http://www.w3.org/2005/Atom}updated")
            items.append({"id": guid, "title": title, "date": _short_date(date), "url": link})
    digest = "\n".join(f"{i['id']} | {i['title']} | {i['date']}" for i in items)
    fp = sha(",".join(str(i["id"]) for i in items))
    return fp, digest, items


def fetch_page(src: dict) -> tuple[str, str, list[dict]]:
    text = html_to_text(http_get(src["url"]))
    return sha(text), text, []


def fetch_json(src: dict) -> tuple[str, str, list[dict]]:
    data = json.loads(http_get(src["url"]))
    text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
    return sha(text), text, []


FETCHERS = {
    "github_releases": fetch_github_releases,
    "rss": fetch_rss,
    "page": fetch_page,
    "json": fetch_json,
}


def _findtext(node, tag: str) -> str:
    el = node.find(tag)
    if el is not None and el.text:
        return el.text.strip()
    # namespaced fallback
    for child in node:
        if child.tag.split("}")[-1] == tag.split("}")[-1] and child.text:
            return child.text.strip()
    return ""


def _short_date(value: str) -> str:
    if not value:
        return ""
    m = re.search(r"\d{4}-\d{2}-\d{2}", value)
    if m:
        return m.group(0)
    try:
        return time.strftime("%Y-%m-%d", time.strptime(value[:25], "%a, %d %b %Y %H:%M:%S"))
    except Exception:
        return value.strip()[:10]


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------
def load_state(path: str) -> dict:
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(path: str, state: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)


# --------------------------------------------------------------------------
# Diff / report
# --------------------------------------------------------------------------
def diff_summary(name: str, prev: dict | None, fp: str, text: str, items: list[dict]) -> dict:
    now_ids = [str(i["id"]) for i in items]
    result = {"name": name, "new_items": [], "status": "UNCHANGED", "diff": "", "error": None}
    if prev is None:
        result["status"] = "BASELINE"
        result["new_items"] = items
        return result
    prev_ids = prev.get("item_ids", [])
    new_ids = [i for i in now_ids if i not in prev_ids]
    if new_ids:
        result["status"] = "NEW ITEMS"
        id_set = set(new_ids)
        result["new_items"] = [i for i in items if str(i["id"]) in id_set]
    elif fp != prev.get("fingerprint"):
        result["status"] = "CHANGED"
        old_text = prev.get("text", "")
        if old_text and text:
            d = unified_diff(
                old_text.splitlines()[:400],
                text.splitlines()[:400],
                lineterm="", n=1,
            )
            result["diff"] = "\n".join(list(d)[:80])
    return result


def render_report(results: list[dict], meta: dict) -> str:
    lines = [f"# Competitor watch brief", "", f"Generated: {meta['generated']}", ""]
    changed = [r for r in results if r["status"] in ("NEW ITEMS", "CHANGED", "BASELINE")]
    errors = [r for r in results if r["status"] == "ERROR"]
    lines.append(f"Sources: {len(results)} · changed: {len(changed)} · errors: {len(errors)}")
    lines.append("")
    for r in results:
        lines.append(f"## {r['name']} — {r['status']}")
        if r["error"]:
            lines.append(f"> ERROR: {r['error']}")
        for it in r["new_items"]:
            date = f" ({it['date']})" if it.get("date") else ""
            url = f" — {it['url']}" if it.get("url") else ""
            lines.append(f"- **{it['title']}**{date}{url}")
        if r["diff"]:
            lines.append("")
            lines.append("```diff")
            lines.append(r["diff"])
            lines.append("```")
        lines.append("")
    lines.append("---")
    lines.append("_Raw changes only. Synthesize the brief yourself — see references/brief-template.md._")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Monitor public sources and diff them over time.")
    ap.add_argument("--config", required=True, help="JSON file listing sources")
    ap.add_argument("--state", default=".cw-state", help="state file (baseline) path")
    ap.add_argument("--out", default="brief.md", help="markdown report output path")
    args = ap.parse_args(argv)

    with open(args.config, "r", encoding="utf-8") as f:
        config = json.load(f)
    sources = config.get("sources", config if isinstance(config, list) else [])
    if not sources:
        print("No sources in config.", file=sys.stderr)
        return 2

    state = load_state(args.state)
    new_state = dict(state)
    results = []

    for src in sources:
        name = src.get("name") or src.get("repo") or src.get("url") or "source"
        stype = src.get("type")
        fetcher = FETCHERS.get(stype)
        if not fetcher:
            results.append({"name": name, "status": "ERROR", "new_items": [], "diff": "",
                            "error": f"unknown type '{stype}'"})
            continue
        try:
            fp, text, items = fetcher(src)
        except (urllib.error.URLError, urllib.error.HTTPError, ET.ParseError,
                json.JSONDecodeError, KeyError, OSError) as exc:
            results.append({"name": name, "status": "ERROR", "new_items": [], "diff": "",
                            "error": f"{type(exc).__name__}: {exc}"})
            continue
        except Exception as exc:  # noqa: BLE001
            results.append({"name": name, "status": "ERROR", "new_items": [], "diff": "",
                            "error": f"{type(exc).__name__}: {exc}"})
            continue

        prev = state.get(name)
        res = diff_summary(name, prev, fp, text, items)
        results.append(res)
        new_state[name] = {
            "type": stype,
            "fingerprint": fp,
            "text": text[:20000],
            "item_ids": [str(i["id"]) for i in items],
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        time.sleep(REQUEST_DELAY)

    meta = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    report = render_report(results, meta)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(report)
    save_state(args.state, new_state)

    changed = sum(1 for r in results if r["status"] in ("NEW ITEMS", "CHANGED", "BASELINE"))
    print(f"{changed} changed / {len(results)} sources -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
