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

UA = "Mozilla/5.0 (compatible; JobScanner/1.0; +https://github.com/artembezukh/job-scanner)"
TIMEOUT = 30


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


def extract_jobs(html, selector, base_url):
    soup = BeautifulSoup(html, "html.parser")
    jobs = {}
    for el in soup.select(selector):
        title = " ".join(el.get_text(" ", strip=True).split())
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

    print(f"[{name}]")
    try:
        html = fetch(url)
    except Exception as e:
        print(f"  fetch failed: {e}", file=sys.stderr)
        return

    current = extract_jobs(html, selector, base)
    if not current:
        print("  selector matched 0 jobs - check your selector or the site might be JS-rendered")
        return

    site_seen = seen.get(name)
    if site_seen is None:
        seen[name] = {
            "first_tracked": now(),
            "jobs": {link: {"title": title, "first_seen": now()}
                     for link, title in current.items()},
        }
        telegram_send(
            f"<b>Now tracking: {escape(name)}</b>\n"
            f"Baseline of {len(current)} job(s) recorded. "
            f"You will only get pings for new postings from now on."
        )
        print(f"  baseline: {len(current)} jobs")
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
    print(f"\nDone: scanned {len(sites)} site(s) at {now()}")


if __name__ == "__main__":
    main()
