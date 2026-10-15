#!/usr/bin/env python3
"""Render technical_report.md to the PDF the submission form asks for (max. 6 pages).

Dev-only dependencies: ``pip install markdown playwright && playwright install chromium``.
Usage: python src/build_report_pdf.py [TeamName]   -> <TeamName>_Report.pdf
"""

from __future__ import annotations

import pathlib
import sys

import markdown
from playwright.sync_api import sync_playwright

TRACK = pathlib.Path(__file__).resolve().parent.parent
TEAM = sys.argv[1] if len(sys.argv) > 1 else "INNOVA"

CSS = """
@page { size: A4; margin: 15mm 14mm 15mm 14mm; }
body { font: 9.6pt/1.38 'DejaVu Sans', Arial, sans-serif; color: #111; }
h1 { font-size: 16pt; margin: 0 0 6pt; }
h2 { font-size: 12pt; margin: 12pt 0 4pt; border-bottom: 1px solid #ccc; padding-bottom: 2pt; }
h3 { font-size: 10.5pt; margin: 9pt 0 3pt; }
p, ul { margin: 4pt 0; }
li { margin: 1pt 0; }
code { font: 8.4pt 'DejaVu Sans Mono', monospace; background: #f3f3f3; padding: 0 2px; }
pre { font: 7.6pt/1.25 'DejaVu Sans Mono', monospace; background: #f6f6f6; padding: 6pt;
      border: 1px solid #ddd; white-space: pre; overflow: hidden; }
pre code { background: none; padding: 0; font-size: inherit; }
table { border-collapse: collapse; width: 100%; margin: 5pt 0; font-size: 8.6pt; }
th, td { border: 1px solid #ccc; padding: 2.5pt 4pt; text-align: left; vertical-align: top; }
th { background: #eee; }
"""


def main() -> int:
    md = (TRACK / "technical_report.md").read_text(encoding="utf-8")
    body = markdown.markdown(md, extensions=["tables", "fenced_code"])
    html = f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}</style></head><body>{body}</body></html>"
    out = TRACK / f"{TEAM}_Report.pdf"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(html, wait_until="load")
        page.pdf(path=str(out), format="A4", print_background=True)
        browser.close()
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
