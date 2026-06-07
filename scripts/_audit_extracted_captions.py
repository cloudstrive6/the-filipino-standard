"""One-shot validator audit on the extracted-caption .txt files."""
import re

FORBIDDEN_MARKERS = [
    "Hook:", "Core argument:", "Pillar:", "Source:", "File path:",
    "Brief:", "Post:", "Image prompt", "Scheduled Publish:", "Status:",
    "Notes:", "Character count:", "auto-publish halt", "Verification done",
    "SANITY CHECKLIST",
]
FORBIDDEN_PATHS = ["Z:" + chr(92), "Z:/", "/output/", ".md", ".py"]
FORBIDDEN_URLS = ["http://", "https://", "file:///"]
SEPARATOR_RE = re.compile(r"^\s*---\s*$", re.MULTILINE)

CASES = [
    ("86d30khk6", "output/posts/2026-05-15-oil-shock-rescue-loan-fb.extracted-caption.txt"),
    ("86d30te8y", "output/posts/2026-05-16-ofw-economy-ceiling-fb.extracted-caption.txt"),
    ("86d30w135", "output/posts/2026-05-16-senate-public-trust-shot-fb.extracted-caption.txt"),
]

for label, path in CASES:
    cap = open(path, encoding="utf-8").read()
    issues = []
    for m in FORBIDDEN_MARKERS:
        if m in cap:
            issues.append(f"forbidden marker: {m!r}")
    for p in FORBIDDEN_PATHS:
        if p in cap:
            issues.append(f"forbidden path marker: {p!r}")
    for u in FORBIDDEN_URLS:
        if u in cap:
            issues.append(f"forbidden URL prefix: {u!r}")
    if SEPARATOR_RE.search(cap):
        issues.append("contains a --- separator line")
    if not issues:
        print(f"{label}: PASS (caption is clean for ClickUp description)")
    else:
        print(f"{label}: FAIL")
        for i in issues:
            print(f"  - {i}")
