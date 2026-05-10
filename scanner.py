#!/usr/bin/env python3
"""Scan career pages for new job postings, notify via Telegram."""

import json
import os
import sys
import time
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from urllib.parse import urljoin

import requests
import yaml
from bs4 import BeautifulSoup

ROOT = Path(__file__).parent
SITES_FILE = ROOT / "sites.yaml"
SEEN_FILE = ROOT / "seen.json"

TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
TIMEOUT = 30
COOKIE_BUTTON_TEXTS = ("Accept All", "Accept all", "Accept", "I agree",
                       "Allow all", "Got it", "OK", "I accept",
                       "Agree and continue", "Allow cookies")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_sites():
    with open(SITES_FILE) as f:
        data = yaml.safe_load(f) or {}
    return data.get("sites") or []


def load_seen():
    if SEEN_FILE.exists():
        text = SEEN_FILE.read_text().strip()
        return json.loads(text) if text else {}
    return {}


def save_seen(seen):
    SEEN_FILE.write_text(json.dumps(seen, indent=2, ensure_ascii=False) + "\n")


def fetch(url):
    r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text


_pw = None
_browser = None


def _get_browser():
    """Lazy-init a single shared Playwright browser, reused across all JS sites."""
    global _pw, _browser
    if _browser is not None:
        return _browser
    from playwright.sync_api import sync_playwright
    _pw = sync_playwright().start()
    _browser = _pw.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
    return _browser


def _close_browser():
    global _pw, _browser
    if _browser is not None:
        try:
            _browser.close()
        except Exception:
            pass
        _browser = None
    if _pw is not None:
        try:
            _pw.stop()
        except Exception:
            pass
        _pw = None


_EMPTY_MARKERS = ("no job openings", "no current openings", "no open positions",
                  "no live roles", "no openings at the moment", "no vacancies",
                  "no roles available", "we have no live roles")


def fetch_js(url, wait_selector=None, wait_ms=3000):
    """Render a page with a real browser before reading the HTML."""
    import re as _re
    browser = _get_browser()
    ctx = browser.new_context(
        user_agent=UA,
        viewport={"width": 1280, "height": 900},
        locale="en-GB",
        extra_http_headers={"Accept-Language": "en-GB,en;q=0.9"},
    )
    page = ctx.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(2000)

        # Dismiss any visible cookie / consent button so it doesn't block content
        for txt in COOKIE_BUTTON_TEXTS:
            try:
                page.get_by_role("button", name=_re.compile(f"^{_re.escape(txt)}$", _re.I)).first.click(timeout=1500)
                break
            except Exception:
                continue

        page.wait_for_timeout(1500)

        # Fast path: page already declares it has no openings — skip long waits
        early = page.content().lower()
        if any(m in early for m in _EMPTY_MARKERS):
            return page.content()

        # Wait for the actual job content to appear, then settle
        if wait_selector:
            try:
                page.wait_for_selector(wait_selector, timeout=15000)
            except Exception:
                pass
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        if wait_ms:
            page.wait_for_timeout(wait_ms)
        return page.content()
    finally:
        ctx.close()


_NOISE_SUFFIXES = {"apply", "view", "learn", "more", "details", "read"}


def clean_title(text: str) -> str:
    words = text.split()
    while words and words[-1].lower().strip(".,:;") in _NOISE_SUFFIXES:
        words.pop()
    n = len(words)
    if n >= 2 and n % 2 == 0 and words[:n // 2] == words[n // 2:]:
        words = words[:n // 2]
    return " ".join(words)


def _text_from_aria(el, soup):
    """Read text of element(s) referenced by aria-labelledby (used by Workable etc.)."""
    ids = (el.get("aria-labelledby") or "").split()
    if not ids:
        return ""
    first = soup.find(id=ids[0])
    return " ".join(first.get_text(" ", strip=True).split()) if first else ""


def extract_jobs(html, selector, base_url):
    soup = BeautifulSoup(html, "html.parser")
    jobs = {}
    for el in soup.select(selector):
        raw = " ".join(el.get_text(" ", strip=True).split())
        if not raw:
            raw = _text_from_aria(el, soup)
        if not raw and el.get("aria-label"):
            raw = el.get("aria-label").strip()
        title = clean_title(raw)
        href = el.get("href")
        if not href or not title:
            continue
        link = urljoin(base_url, href)
        jobs[link] = title
    return jobs


def telegram_send(text):
    if not TG_TOKEN or not TG_CHAT:
        print("WARNING: Telegram credentials not set; printing instead:")
        print(text)
        print()
        return
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    for i in range(0, len(text), 4000):
        chunk = text[i:i + 4000]
        r = requests.post(url, json={
            "chat_id": TG_CHAT,
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }, timeout=TIMEOUT)
        if not r.ok:
            print(f"Telegram error {r.status_code}: {r.text}", file=sys.stderr)


def scan_site(site, seen):
    name = site["name"]
    url = site["url"]
    selector = site["selector"]
    base = site.get("base_url", url)

    render = (site.get("render") or "static").lower()
    wait_selector = site.get("wait_selector")

    print(f"[{name}] ({render})")
    try:
        if render in ("js", "playwright", "browser"):
            html = fetch_js(url, wait_selector=wait_selector)
        else:
            html = fetch(url)
    except Exception as e:
        print(f"  fetch failed: {e}", file=sys.stderr)
        return

    current = extract_jobs(html, selector, base)

    # Tell apart "site has no openings right now" from "selector is wrong / page broken"
    page_text = " ".join(BeautifulSoup(html, "html.parser").get_text(" ", strip=True).split()).lower()
    explicitly_empty = any(p in page_text for p in (
        "no job openings", "no current openings", "no open positions",
        "no live roles", "no openings at the moment", "no vacancies",
        "no roles available", "we have no live roles",
    ))

    if not current and not explicitly_empty:
        print("  selector matched 0 jobs - check your selector or the site might be JS-rendered")
        soup = BeautifulSoup(html, "html.parser")
        anchors = soup.find_all("a", href=True)
        print(f"  debug: rendered HTML has {len(anchors)} <a> tags. Sample hrefs:")
        seen_h = set()
        for a in anchors:
            h = a["href"]
            if h in seen_h or h.startswith("#") or h.startswith("javascript:"):
                continue
            seen_h.add(h)
            txt = " ".join(a.get_text(" ", strip=True).split())[:60]
            print(f"    {h[:90]}  | text: {txt}")
            if len(seen_h) >= 25:
                break
        return

    site_seen = seen.get(name)
    if site_seen is None:
        seen[name] = {
            "first_tracked": now(),
            "jobs": {link: {"title": title, "first_seen": now()}
                     for link, title in current.items()},
        }
        if current:
            lines = [f"<b>Now tracking: {escape(name)}</b>",
                     f"Baseline of {len(current)} job(s) recorded. "
                     f"You will only get pings for new postings from now on."]
            if len(current) <= 5:
                lines.append("")
                for link, title in current.items():
                    lines.append(f'- <a href="{escape(link)}">{escape(title)}</a>')
            telegram_send("\n".join(lines))
            print(f"  baseline: {len(current)} jobs")
        else:
            telegram_send(
                f"<b>Now tracking: {escape(name)}</b>\n"
                f"No openings right now. You will be pinged when one appears."
            )
            print("  baseline: 0 jobs (empty board, will track for new ones)")
        return

    known = site_seen["jobs"]
    new_links = [l for l in current if l not in known]
    for link in new_links:
        known[link] = {"title": current[link], "first_seen": now()}

    if new_links:
        lines = [f"<b>New jobs at {escape(name)}</b>", ""]
        for link in new_links:
            lines.append(f'- <a href="{escape(link)}">{escape(current[link])}</a>')
        telegram_send("\n".join(lines))
        print(f"  {len(new_links)} new (of {len(current)} total)")
    else:
        print(f"  no new (of {len(current)} total)")


def main():
    sites = load_sites()
    if not sites:
        print("No sites configured. Edit sites.yaml to add some.")
        return

    seen = load_seen()
    for i, site in enumerate(sites):
        if i > 0:
            time.sleep(1)
        try:
            scan_site(site, seen)
        except Exception as e:
            print(f"  unexpected error on '{site.get('name', '?')}': {e}", file=sys.stderr)

    save_seen(seen)
    _close_browser()
    print(f"\nDone: scanned {len(sites)} site(s) at {now()}")


if __name__ == "__main__":
    main()
