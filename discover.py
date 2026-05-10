#!/usr/bin/env python3
"""Inspect a single page (rendered with Playwright) to help find a CSS selector.

Usage: DISCOVER_URL=https://example.com/careers python discover.py
"""

import os
import re
import sys
from collections import Counter
from urllib.parse import urlparse

from bs4 import BeautifulSoup

import scanner

URL = os.environ.get("DISCOVER_URL", "").strip()
WAIT_SELECTOR = os.environ.get("DISCOVER_WAIT_SELECTOR", "").strip() or None


def main():
    if not URL:
        print("Set DISCOVER_URL in env.")
        sys.exit(1)

    print(f"Fetching {URL} with Playwright...")
    try:
        html = scanner.fetch_js(URL, wait_selector=WAIT_SELECTOR)
    finally:
        scanner._close_browser()

    print(f"Rendered HTML: {len(html)} chars")
    soup = BeautifulSoup(html, "html.parser")
    anchors = [a for a in soup.find_all("a", href=True)
               if not a["href"].startswith(("#", "javascript:", "mailto:", "tel:"))]
    print(f"Anchor tags: {len(anchors)}")

    # Group anchor hrefs by their "shape" - the path with digits replaced
    pat_counts = Counter()
    samples = {}
    for a in anchors:
        href = a["href"]
        path = urlparse(href).path or href
        shape = re.sub(r"\d+", "N", path)
        shape = re.sub(r"[a-f0-9]{8,}", "HEX", shape, flags=re.IGNORECASE)
        pat_counts[shape] += 1
        samples.setdefault(shape, []).append(
            (href, " ".join(a.get_text(" ", strip=True).split())[:80])
        )

    print("\n=== Top 20 anchor patterns (count, shape, examples) ===")
    for shape, n in pat_counts.most_common(20):
        if n < 2:
            continue
        ex = samples[shape][0]
        print(f"  [{n:3}] {shape}")
        print(f"        href: {ex[0][:90]}")
        print(f"        text: {ex[1]}")

    print("\n=== Job-title-shaped anchor texts (first 25) ===")
    role_pat = re.compile(
        r"(Senior|Junior|Mid|Midweight|Lead|Head|Group|Global|Principal|Staff|Chief|Associate)?\s*"
        r"[A-Z][\w\s&/\-,]{2,40}"
        r"(Designer|Director|Manager|Strategist|Producer|Writer|Engineer|Editor|Planner|"
        r"Officer|Specialist|Executive|Coordinator|Analyst|Consultant|Architect|Intern|"
        r"Assistant|Copywriter|Lead|Head)"
    )
    n = 0
    seen_t = set()
    for a in anchors:
        text = " ".join(a.get_text(" ", strip=True).split())
        if 5 < len(text) < 80 and role_pat.search(text) and text not in seen_t:
            seen_t.add(text)
            classes = " ".join(a.get("class") or [])[:60]
            print(f"  href={a['href'][:60]}  text={text[:50]}  classes={classes}")
            n += 1
            if n >= 25:
                break
    if n == 0:
        print("  (none found - site probably loads jobs from a separate API not triggered by initial render)")


if __name__ == "__main__":
    main()
