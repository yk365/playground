#!/usr/bin/env python3
"""
Scrape thegreatcourses.com subject → category → course structure into JSON.

IMPORTANT: Run this on YOUR OWN MACHINE (not a server). The site uses Akamai
bot protection that blocks datacenter IPs — your home/office IP works fine.

Usage:
    pip install requests beautifulsoup4

    # Test a single subject and dump raw HTML for debugging
    python3 scrape_courses.py --cookies cookies.json --subject art --debug

    # Full scrape
    python3 scrape_courses.py --cookies cookies.json --out courses.json

cookies.json: export from Chrome using the "Cookie-Editor" extension →
              click Export → "Export All" → save as cookies.json
"""

import argparse
import json
import sys
import time
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from urllib.parse import urljoin

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    sys.exit("Missing deps. Run:  pip install requests beautifulsoup4")

BASE = "https://plus.thegreatcourses.com"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://plus.thegreatcourses.com/",
}


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def load_json_cookies(path: str) -> requests.cookies.RequestsCookieJar:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    jar = requests.cookies.RequestsCookieJar()
    for c in data:
        jar.set(
            c["name"],
            c["value"],
            domain=c.get("domain", "").lstrip("."),
            path=c.get("path", "/"),
        )
    return jar


def make_session(cookie_file: str | None) -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    if cookie_file:
        raw = Path(cookie_file).read_text(encoding="utf-8").lstrip()
        if raw.startswith("[") or raw.startswith("{"):
            s.cookies = load_json_cookies(cookie_file)
        else:
            jar = MozillaCookieJar(cookie_file)
            jar.load(ignore_discard=True, ignore_expires=True)
            s.cookies = jar
    return s


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def fetch_html(session: requests.Session, url: str, debug_dir: Path | None) -> BeautifulSoup:
    r = session.get(url, timeout=20)
    if debug_dir:
        slug = url.rstrip("/").split("/")[-1] or "index"
        (debug_dir / f"{slug}.html").write_text(r.text, encoding="utf-8")
        print(f"  [debug] saved {slug}.html ({len(r.text)} bytes, status {r.status_code})")
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


# ---------------------------------------------------------------------------
# Scraping logic — multiple strategies, most-specific first
# ---------------------------------------------------------------------------

def scrape_subjects(session: requests.Session, debug_dir: Path | None) -> list[dict]:
    soup = fetch_html(session, f"{BASE}/allsubjects", debug_dir)
    subjects = []
    seen = set()

    # Strategy 1: any <a> whose href matches /allsubjects/<slug>
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/allsubjects/" in href:
            slug = href.rstrip("/").split("/")[-1]
            if slug and slug not in seen:
                seen.add(slug)
                name = a.get_text(strip=True) or slug
                subjects.append({"name": name, "slug": slug, "url": urljoin(BASE, href)})

    # Strategy 2: text nodes "View All" whose nearest ancestor has an href
    if not subjects:
        for node in soup.find_all(string=lambda t: t and t.strip().lower() == "view all"):
            link = node.find_parent("a")
            if not link:
                link = node.find_parent(attrs={"href": True})
            if link and "/allsubjects/" in link.get("href", ""):
                href = link["href"]
                slug = href.rstrip("/").split("/")[-1]
                if slug and slug not in seen:
                    seen.add(slug)
                    # Subject name is usually a sibling/nearby element
                    heading = link.find_previous(["h2", "h3", "h4", "span", "p"])
                    name = heading.get_text(strip=True) if heading else slug
                    subjects.append({"name": name, "slug": slug, "url": urljoin(BASE, href)})

    return subjects


def scrape_subject_page(session: requests.Session, subject: dict, debug_dir: Path | None) -> dict:
    soup = fetch_html(session, subject["url"], debug_dir)
    categories = []
    current_cat = None

    # Walk the document in order: headings open a new category, links go into it
    for el in soup.find_all(["h1", "h2", "h3", "h4", "h5", "a"]):
        tag = el.name

        if tag in ("h1", "h2", "h3", "h4", "h5"):
            text = el.get_text(strip=True)
            # Skip navigation / page-level headings that aren't category names
            if not text or text.lower() in ("view all", "all subjects"):
                continue
            # A heading that follows a "View All" link pattern is a category
            current_cat = {"name": text, "courses": []}
            categories.append(current_cat)

        elif tag == "a" and current_cat is not None:
            href = el.get("href", "")
            title = el.get_text(strip=True)
            # Typical course URL patterns
            if any(p in href for p in ("/courses/", "/series/", "/video/", "/collection/")):
                if title and not any(c["title"] == title for c in current_cat["courses"]):
                    current_cat["courses"].append({"title": title, "url": urljoin(BASE, href)})

    # Fallback: no headings found — bucket everything under one category
    if not categories or all(len(c["courses"]) == 0 for c in categories):
        all_courses = []
        seen_titles: set[str] = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if any(p in href for p in ("/courses/", "/series/", "/video/", "/collection/")):
                title = a.get_text(strip=True)
                if title and title not in seen_titles:
                    seen_titles.add(title)
                    all_courses.append({"title": title, "url": urljoin(BASE, href)})
        if all_courses:
            categories = [{"name": subject["name"], "courses": all_courses}]

    return {**subject, "categories": categories}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--cookies", metavar="FILE", help="JSON cookies file exported from browser")
    ap.add_argument("--out", metavar="FILE", default="courses.json", help="Output JSON (default: courses.json)")
    ap.add_argument("--delay", type=float, default=1.0, help="Seconds between requests (default: 1.0)")
    ap.add_argument("--subject", help="Scrape only this slug, e.g. --subject art")
    ap.add_argument("--debug", action="store_true", help="Save raw HTML of each page to ./debug_html/")
    args = ap.parse_args()

    debug_dir: Path | None = None
    if args.debug:
        debug_dir = Path("debug_html")
        debug_dir.mkdir(exist_ok=True)
        print(f"Debug HTML will be saved to {debug_dir.resolve()}/")

    session = make_session(args.cookies)

    if args.subject:
        subjects = [{
            "name": args.subject,
            "slug": args.subject,
            "url": f"{BASE}/allsubjects/{args.subject}",
        }]
    else:
        print("Fetching subject list...", flush=True)
        subjects = scrape_subjects(session, debug_dir)
        print(f"  Found {len(subjects)} subjects", flush=True)
        if not subjects:
            print("  No subjects found. Run with --debug to inspect the HTML and report back.")
            sys.exit(1)

    results = []
    for i, subj in enumerate(subjects, 1):
        print(f"[{i}/{len(subjects)}] {subj['name']} ...", flush=True)
        try:
            data = scrape_subject_page(session, subj, debug_dir)
            n_cats = len(data.get("categories", []))
            n_courses = sum(len(c["courses"]) for c in data.get("categories", []))
            print(f"  {n_cats} categories, {n_courses} courses")
            results.append(data)
        except requests.HTTPError as e:
            print(f"  ERROR {e}")
            results.append({**subj, "categories": [], "error": str(e)})
        if i < len(subjects):
            time.sleep(args.delay)

    out_path = Path(args.out)
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    total_cats = sum(len(s.get("categories", [])) for s in results)
    total_courses = sum(len(c["courses"]) for s in results for c in s.get("categories", []))
    print(f"\nDone — {len(results)} subjects / {total_cats} categories / {total_courses} courses")
    print(f"Output → {out_path.resolve()}")


if __name__ == "__main__":
    main()
