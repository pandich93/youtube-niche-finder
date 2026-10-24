#!/usr/bin/env python3
"""Check the relative links in the repository's Markdown files.

Every `[text](path)`, `<img src="path">` and `<a href="path">` in a tracked
.md file must point at a file or folder that git tracks -- not just one that
exists on disk: `.gitignore` skips `docs/*` except the pages it lists, so a
new page linked from four places could stay local and every link to it
would be broken on GitHub. A `#section` after a .md link must be a heading
there (GitHub's anchor rules). Links into code blocks and external URLs are
not checked.

    python3 scripts/check_doc_links.py      # exit 1 and list what is broken
"""
import re
import subprocess
import sys
from pathlib import PurePosixPath
from urllib.parse import unquote

INLINE = re.compile(r"!?\[(?:[^\]\[]|\[[^\]]*\])*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
HTML = re.compile(r"""<(?:img|a|source)\b[^>]*?\b(?:src|href)\s*=\s*["']([^"']+)["']""", re.I)
FENCE = re.compile(r"^\s*(```|~~~)")
INLINE_CODE = re.compile(r"`[^`\n]*`")
HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
HTML_ANCHOR = re.compile(r"""<a\s+(?:name|id)\s*=\s*["']([^"']+)["']""", re.I)
SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*:", re.I)


def tracked():
    out = subprocess.run(["git", "ls-files", "-z"], capture_output=True, check=True).stdout
    files = {p for p in out.decode("utf-8").split("\0") if p}
    dirs = {str(parent) for f in files for parent in PurePosixPath(f).parents if str(parent) != "."}
    return files, dirs


def prose_lines(text):
    """Lines outside fenced code blocks, inline code blanked out."""
    fenced = False
    for line in text.splitlines():
        if FENCE.match(line):
            fenced = not fenced
            continue
        if not fenced:
            yield INLINE_CODE.sub("", line)


def slug(heading):
    """GitHub's anchor for a heading: lower case, markup and punctuation
    dropped, spaces to hyphens."""
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", heading)       # links -> their text
    text = re.sub(r"<[^>]+>", "", text).replace("`", "")
    text = re.sub(r"[*~]", "", text).strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def anchors(text):
    seen, out = {}, set()
    fenced = False
    for line in text.splitlines():
        if FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        out.update(HTML_ANCHOR.findall(line))
        m = HEADING.match(line)
        if not m:
            continue
        base = slug(m.group(2))
        n = seen.get(base, 0)
        seen[base] = n + 1
        out.add(base if n == 0 else f"{base}-{n}")
    return out


def resolve(here, path):
    parts = []
    for part in (PurePosixPath(path[1:]) if path.startswith("/") else here / path).parts:
        if part == "..":
            if parts:
                parts.pop()
        elif part != ".":
            parts.append(part)
    return "/".join(parts)


def main():
    files, dirs = tracked()
    docs = sorted(f for f in files if f.endswith(".md"))
    cache, broken = {}, []

    def anchors_of(path):
        if path not in cache:
            with open(path, encoding="utf-8") as fh:
                cache[path] = anchors(fh.read())
        return cache[path]

    for doc in docs:
        with open(doc, encoding="utf-8") as fh:
            text = fh.read()
        here = PurePosixPath(doc).parent
        for line in prose_lines(text):
            for target in INLINE.findall(line) + HTML.findall(line):
                if SCHEME.match(target) or target.startswith("//"):
                    continue
                path, _, frag = target.partition("#")
                path = unquote(path.split("?", 1)[0])
                frag = unquote(frag).lower()
                if not path:                       # #section in the same file
                    if frag and frag not in anchors_of(doc):
                        broken.append(f"{doc}: #{frag} is not a heading here")
                    continue
                target_path = resolve(here, path)
                if target_path not in files and target_path.rstrip("/") not in dirs:
                    broken.append(f"{doc}: {target} -> {target_path} is not in the repository")
                elif frag and target_path.endswith(".md") and frag not in anchors_of(target_path):
                    broken.append(f"{doc}: {target} -> no heading #{frag} in {target_path}")
    for line in broken:
        print(line, file=sys.stderr)
    if broken:
        print(f"{len(broken)} broken link(s) in {len(docs)} Markdown files", file=sys.stderr)
        return 1
    print(f"all relative links in {len(docs)} Markdown files resolve")
    return 0


if __name__ == "__main__":
    sys.exit(main())
