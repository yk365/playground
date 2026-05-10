#!/usr/bin/env python3
"""Parse tab-indented course file into structured JSON."""

import json
import re
import sys


def parse_item_line(line: str) -> dict | None:
    """Parse a leaf item line: 'A1 Short desc\tA1 Long desc'"""
    parts = line.split("\t", 1)
    text = parts[0].strip()
    if not text:
        return None
    match = re.match(r"^([A-Z]\d+)\s+(.+)$", text)
    if match:
        return {
            "id": match.group(1),
            "summary": match.group(2).strip(),
            "description": parts[1].strip() if len(parts) > 1 else "",
        }
    return {"id": None, "summary": text, "description": ""}


def parse_file(path: str) -> list[dict]:
    courses = []
    current_course = None
    current_category = None

    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            if not line.strip():
                continue

            # Count leading tabs to determine depth
            stripped = line.lstrip("\t")
            depth = len(line) - len(stripped)

            if depth == 0:
                current_course = {"name": stripped.strip(), "categories": []}
                courses.append(current_course)
                current_category = None

            elif depth == 1:
                if current_course is None:
                    continue
                current_category = {"name": stripped.strip(), "items": []}
                current_course["categories"].append(current_category)

            else:  # depth >= 2 → item row
                if current_category is None:
                    continue
                item = parse_item_line(stripped)
                if item:
                    current_category["items"].append(item)

    return courses


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "test.txt"
    courses = parse_file(path)
    print(json.dumps(courses, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
