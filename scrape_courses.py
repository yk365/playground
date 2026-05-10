#!/usr/bin/env python3
"""
Scrape thegreatcourses.com subject → category → course structure into JSON.

Usage:
    python3 scrape_courses.py                        # tries without auth
    python3 scrape_courses.py --cookies cookies.txt  # use exported cookies
    python3 scrape_courses.py --cookies cookies.txt --out courses.json

How to export cookies.txt:
    Chrome/Edge: install "Get cookies.txt LOCALLY" extension → click Export
    Firefox:     install "cookies.txt" extension → click Export
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
    sys.exit("Missing dependencies. Run:  pip install requests beautifulsoup4")

BASE = "https://plus.thegreatcourses.com"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def make_session(cookie_file: str | None) -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    if cookie_file:
        jar = MozillaCookieJar(cookie_file)
        jar.load(ignore_discard=True, ignore_expires=True)
        s.cookies = jar
    return s


def get_soup(session: requests.Session, url: str) -> BeautifulSoup:
    r = session.get(url, timeout=20)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


def scrape_subjects(session: requests.Session) -> list[dict]:
    """Return [{name, slug, url}] for every subject on /allsubjects."""
    soup = get_soup(session, f"{BASE}/allsubjects")
    subjects = []

    # Look for subject links — common patterns on course catalog sites
    for a in soup.find_all("a", href=True):
        href = a["href"]
        # Subject pages follow /allsubjects/<slug>
        if "/allsubjects/" in href and href.count("/") >= 2:
            slug = href.rstrip("/").split("/")[-1]
            name = a.get_text(strip=True)
            if slug and name and not any(s["slug"] == slug for s in subjects):
                subjects.append({
                    "name": name,
                    "slug": slug,
                    "url": urljoin(BASE, href),
                })

    if not subjects:
        # Fallback: look for buttons/divs with "View All" pattern near subject names
        for btn in soup.find_all(string=lambda t: t and "view all" in t.lower()):
            parent = btn.find_parent("a") or btn.find_parent(attrs={"href": True})
            if parent and parent.get("href"):
                href = parent["href"]
                if "/allsubjects/" in href:
                    slug = href.rstrip("/").split("/")[-1]
                    label = btn.find_parent().find_previous_sibling()
                    name = label.get_text(strip=True) if label else slug
                    subjects.append({"name": name, "slug": slug, "url": urljoin(BASE, href)})

    return subjects


def scrape_subject_page(session: requests.Session, subject: dict) -> dict:
    """Return subject dict with categories:[{name, courses:[...]}]"""
    soup = get_soup(session, subject["url"])
    categories = []
    current_cat = None

    # Walk all headings and course cards in document order
    for el in soup.find_all(["h2", "h3", "h4", "a"]):
        tag = el.name

        if tag in ("h2", "h3", "h4"):
            text = el.get_text(strip=True)
            if text and "view all" not in text.lower():
                current_cat = {"name": text, "courses": []}
                categories.append(current_cat)

        elif tag == "a" and current_cat is not None:
            href = el.get("href", "")
            # Course links typically contain /courses/ or /series/
            if "/courses/" in href or "/series/" in href or "/video/" in href:
                title = el.get_text(strip=True)
                if title and not any(c["title"] == title for c in current_cat["courses"]):
                    current_cat["courses"].append({
                        "title": title,
                        "url": urljoin(BASE, href),
                    })

    # If page structure didn't yield categories, group everything under subject name
    if not categories:
        all_courses = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/courses/" in href or "/series/" in href or "/video/" in href:
                title = a.get_text(strip=True)
                if title and not any(c["title"] == title for c in all_courses):
                    all_courses.append({"title": title, "url": urljoin(BASE, href)})
        if all_courses:
            categories = [{"name": subject["name"], "courses": all_courses}]

    return {**subject, "categories": categories}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cookies", metavar="FILE", help="Netscape cookies.txt from your browser")
    ap.add_argument("--out", metavar="FILE", default="courses.json", help="Output JSON file (default: courses.json)")
    ap.add_argument("--delay", type=float, default=1.0, help="Seconds between requests (default: 1.0)")
    ap.add_argument("--subject", help="Scrape only this subject slug (e.g. art) — for testing")
    args = ap.parse_args()

    session = make_session(args.cookies)

    if args.subject:
        subjects = [{"name": args.subject, "slug": args.subject,
                     "url": f"{BASE}/allsubjects/{args.subject}"}]
    else:
        print("Fetching subject list...", flush=True)
        subjects = scrape_subjects(session)
        print(f"  Found {len(subjects)} subjects", flush=True)

    results = []
    for i, subj in enumerate(subjects, 1):
        print(f"[{i}/{len(subjects)}] {subj['name']} ...", flush=True)
        try:
            data = scrape_subject_page(session, subj)
            results.append(data)
        except requests.HTTPError as e:
            print(f"  ERROR: {e}", flush=True)
            results.append({**subj, "categories": [], "error": str(e)})
        if i < len(subjects):
            time.sleep(args.delay)

    out_path = Path(args.out)
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    total_cats = sum(len(s.get("categories", [])) for s in results)
    total_courses = sum(
        len(c["courses"]) for s in results for c in s.get("categories", [])
    )
    print(f"\nDone. {len(results)} subjects, {total_cats} categories, {total_courses} courses")
    print(f"Output: {out_path.resolve()}")


if __name__ == "__main__":
    main()
