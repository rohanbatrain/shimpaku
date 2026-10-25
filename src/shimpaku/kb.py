"""Shim's knowledge base: this repository's own engineering documents, searchable.

What is indexed is an allow-list, all of it already public in the repository: the README,
SECURITY, DEMO and CONTRACT, ``docs/*.md``, ``docs/build/*.md``, ``docs/research/*.md``, and the
module docstring of every Python module under ``src/shimpaku`` and ``bench/virga``. Nothing from
``.local/``, the environment, the store or a device. Markdown is split at its headings, docstrings
are one chunk per module, and every chunk is passed through ``scrub`` (addresses, key-like
strings) before it is indexed, so a search can only return what a reader of the repository could.

Ranking is plain BM25 over lower-cased word tokens with a small boost for title hits: no
embeddings, no network, no new dependency. The index is built once, lazily, and is small (a few
hundred chunks).
"""

from __future__ import annotations

import ast
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = ["README.md", "SECURITY.md", "DEMO.md", "CONTRACT.md", "docs/*.md", "docs/build/*.md", "docs/research/*.md"]
CODE = ["src/shimpaku/**/*.py", "bench/virga/**/*.py"]
MAX_CHUNK = 2400  # characters kept per chunk; long sections are split on paragraphs

_WORD = re.compile(r"[a-z0-9][a-z0-9_\-./]*")
_KEYLIKE = re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_\-]{8,}\b|\b[A-Fa-f0-9]{40,}\b")
_STOP = set("a an and are as at be by for from has have how i in is it its of on or that the this to was what when where which who why will with you your does do can".split())


@dataclass(frozen=True)
class Chunk:
    path: str
    section: str
    text: str


def scrub(text: str) -> str:
    from shimpaku.service import scrub_hosts
    return _KEYLIKE.sub("<redacted>", scrub_hosts(text))


def _tokens(s: str) -> list[str]:
    return [t.strip("./-") for t in _WORD.findall(s.lower()) if t.strip("./-") and t not in _STOP]


def _split_md(path: str, text: str) -> list[Chunk]:
    out: list[Chunk] = []
    trail: list[str] = []  # heading path, e.g. ["Guardrails", "G3"]
    buf: list[str] = []

    def flush() -> None:
        body = "\n".join(buf).strip()
        if not body:
            return
        sec = " › ".join(trail) or Path(path).stem
        while body:
            if len(body) <= MAX_CHUNK:
                out.append(Chunk(path, sec, body))
                break
            cut = body.rfind("\n\n", 0, MAX_CHUNK)
            cut = cut if cut > MAX_CHUNK // 3 else MAX_CHUNK
            out.append(Chunk(path, sec, body[:cut].strip()))
            body = body[cut:].strip()

    fence = False
    for line in text.splitlines():
        if line.startswith("```"):
            fence = not fence
        m = None if fence else re.match(r"^(#{1,4})\s+(.*)", line)
        if m:
            flush()
            buf = []
            level = len(m.group(1))
            trail = trail[: level - 1] + [m.group(2).strip()]
        else:
            buf.append(line)
    flush()
    return out


def _docstring(path: Path) -> str | None:
    try:
        return ast.get_docstring(ast.parse(path.read_text(encoding="utf-8")))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return None


def build(root: Path = ROOT) -> list[Chunk]:
    chunks: list[Chunk] = []
    seen: set[Path] = set()
    for pat in DOCS:
        for f in sorted(root.glob(pat)):
            if f in seen or not f.is_file():
                continue
            seen.add(f)
            rel = f.relative_to(root).as_posix()
            chunks += _split_md(rel, scrub(f.read_text(encoding="utf-8", errors="replace")))
    for pat in CODE:
        for f in sorted(root.glob(pat)):
            doc = _docstring(f)
            if doc and len(doc) > 60:
                rel = f.relative_to(root).as_posix()
                chunks.append(Chunk(rel, "module docstring", scrub(doc)[:MAX_CHUNK]))
    return chunks


class Index:
    def __init__(self, chunks: list[Chunk]):
        self.chunks = chunks
        self.toks = [_tokens(f"{c.section} {c.text}") for c in chunks]
        self.heads = [set(_tokens(f"{c.path} {c.section}")) for c in chunks]
        self.avg = sum(map(len, self.toks)) / max(1, len(self.toks))
        df: Counter[str] = Counter()
        for t in self.toks:
            df.update(set(t))
        n = len(chunks)
        self.idf = {w: math.log(1 + (n - d + 0.5) / (d + 0.5)) for w, d in df.items()}

    def search(self, query: str, k: int = 5) -> list[tuple[float, Chunk]]:
        q = list(dict.fromkeys(_tokens(query)))
        if not q:
            return []
        scored = []
        for i, toks in enumerate(self.toks):
            tf = Counter(toks)
            s = 0.0
            for w in q:
                f = tf.get(w, 0)
                if f:
                    s += self.idf.get(w, 0) * f * 2.2 / (f + 1.2 * (0.25 + 0.75 * len(toks) / self.avg))
                if w in self.heads[i]:
                    s += 1.5 * self.idf.get(w, 0)
            if s > 0:
                scored.append((s, self.chunks[i]))
        scored.sort(key=lambda x: -x[0])
        return scored[:k]


_index: Index | None = None


def index() -> Index:
    global _index
    if _index is None:
        _index = Index(build())
    return _index


def search(query: str, k: int = 5) -> dict:
    hits = index().search(query, max(1, min(int(k), 8)))
    return {"query": query, "results": [{"path": c.path, "section": c.section, "score": round(s, 2),
                                         "text": c.text[:1200]} for s, c in hits],
            "indexed_chunks": len(index().chunks)}


def read(path: str, section: str | None = None) -> dict:
    """The full text of one indexed document (or one of its sections), from the index only."""
    rows = [c for c in index().chunks if c.path == path]
    if not rows:
        return {"error": f"{path!r} is not in the knowledge base", "paths": sorted({c.path for c in index().chunks})[:80]}
    if section:
        want = section.lower()
        pick = [c for c in rows if want in c.section.lower()]
        rows = pick or rows
    text = "\n\n".join(f"## {c.section}\n{c.text}" for c in rows)
    return {"path": path, "sections": [c.section for c in rows][:40], "text": text[:8000],
            "truncated": len(text) > 8000}
