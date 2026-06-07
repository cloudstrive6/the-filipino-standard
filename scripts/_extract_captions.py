"""Extract clean caption from each .md source file (no metadata, no title)."""
import re


def extract_caption(path: str) -> str:
    txt = open(path, encoding="utf-8").read()
    lines = txt.split("\n")
    body: list[str] = []
    past_header = False
    for i, line in enumerate(lines):
        if not past_header:
            # Skip markdown title (line 1 starts with '# ')
            if i == 0 and line.startswith("# "):
                continue
            # Skip bold-key metadata lines: **Key:** value
            if re.match(r"^\*\*[A-Z][^*]*:\*\*", line):
                continue
            # Skip blank lines while still in the header
            if not line.strip():
                continue
            # Skip first --- separator (after metadata)
            if line.strip() == "---":
                continue
            # First non-header line - body begins here
            past_header = True
        # Inside body: drop any --- separators (they introduce trailing
        # hashtag blocks but the validator forbids --- in descriptions)
        if line.strip() == "---":
            continue
        body.append(line)
    # Trim leading/trailing blanks
    while body and not body[0].strip():
        body.pop(0)
    while body and not body[-1].strip():
        body.pop()
    return "\n".join(body)


CASES = [
    ("86d30khk6", "output/posts/2026-05-15-oil-shock-rescue-loan-fb.md"),
    ("86d30te8y", "output/posts/2026-05-16-ofw-economy-ceiling-fb.md"),
    ("86d30w135", "output/posts/2026-05-16-senate-public-trust-shot-fb.md"),
]

EM = chr(0x2014)
EN = chr(0x2013)

for label, path in CASES:
    cap = extract_caption(path)
    em = cap.count(EM)
    en = cap.count(EN)
    out = path.replace(".md", ".extracted-caption.txt")
    open(out, "w", encoding="utf-8").write(cap)
    print(f"{label}: {len(cap)} chars, em={em}, en={en}, saved={out}")
    print(f"  first 80: {cap[:80]!r}")
    print(f"  last  80: {cap[-80:]!r}")
    print()
