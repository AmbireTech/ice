#!/usr/bin/env python3
"""Build the local help center preview: articles.json + template.html -> index.html.

articles.json is the source and the export: sections, each with articles whose body
is a small Markdown subset (paragraphs, "- " and "1. " lists, "### ", "> " notes,
**bold**, `code`, [links](/help/<section>/<article>/)). A section with
"style": "airgap" also carries "quickstart" steps and is drawn as its own mode.
An article may carry "status": "needs-answer" while details wait on the owner.

    python3 docs/help/build.py          # writes docs/help/index.html
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LINK = re.compile(r"\]\((/[^)\s]*)\)")


def validate(data):
    errors = []
    base = data["basePath"]
    refs = set()
    seen_sections = set()
    for sec in data["sections"]:
        if sec["id"] in seen_sections:
            errors.append(f"duplicate section id {sec['id']}")
        seen_sections.add(sec["id"])
        if sec.get("style") not in ("standard", "airgap"):
            errors.append(f"{sec['id']}: style must be standard or airgap")
        if sec.get("style") == "airgap" and not sec.get("quickstart"):
            errors.append(f"{sec['id']}: an airgap section needs quickstart steps")
        seen = set()
        for art in sec["articles"]:
            if art["id"] in seen:
                errors.append(f"duplicate article id {sec['id']}/{art['id']}")
            seen.add(art["id"])
            refs.add(f"{sec['id']}/{art['id']}")
            if art.get("status") not in (None, "needs-answer"):
                errors.append(f"{sec['id']}/{art['id']}: unknown status {art['status']}")
    for sec in data["sections"]:
        for art in sec["articles"]:
            for url in LINK.findall(art["body"]):
                if not url.startswith(base):
                    errors.append(f"{sec['id']}/{art['id']}: site link outside {base}: {url}")
                    continue
                target = "/".join(p for p in url[len(base):].split("/") if p)
                if target not in refs and target not in seen_sections:
                    errors.append(f"{sec['id']}/{art['id']}: broken link {url}")
    for ref in data["popular"]:
        if ref not in refs:
            errors.append(f"popular: no article {ref}")
    return errors


def main():
    data = json.loads((HERE / "articles.json").read_text())
    errors = validate(data)
    if errors:
        sys.exit("articles.json:\n  " + "\n  ".join(errors))
    template = (HERE / "template.html").read_text()
    marker = "<!--HELP_DATA-->"
    if template.count(marker) != 1:
        sys.exit(f"template.html: expected one {marker}")
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    (HERE / "index.html").write_text(template.replace(marker, payload))
    articles = sum(len(s["articles"]) for s in data["sections"])
    drafts = [f"{s['id']}/{a['id']}" for s in data["sections"] for a in s["articles"] if a.get("status")]
    print(f"index.html: {len(data['sections'])} sections, {articles} articles"
          + (f"; needs an answer: {', '.join(drafts)}" if drafts else ""))


if __name__ == "__main__":
    main()
