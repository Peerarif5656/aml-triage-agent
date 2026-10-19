"""Policy retrieval (the RAG component).

The policy document is split into sections by its `## [POL-...]` headings and
indexed with BM25. BM25 keeps the demo dependency-free and fully explainable;
in production this would sit alongside an embedding index (hybrid retrieval),
which is a drop-in change behind `PolicyIndex.search`.
"""
from __future__ import annotations

import math
import re
from collections import Counter

STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "is", "are", "be", "for", "on", "with", "by", "as",
        "that", "this", "it", "at", "from", "can", "should", "must", "not", "any", "where", "when", "which",
        "such", "their", "its", "than", "into", "within", "has", "have", "was", "were", "been", "if", "so"}


def _tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    out = []
    for w in words:
        if w in STOP or len(w) < 2:
            continue
        for suffix in ("ing", "ies", "es", "s", "ed"):
            if w.endswith(suffix) and len(w) - len(suffix) >= 4:
                w = w[: -len(suffix)] + ("y" if suffix == "ies" else "")
                break
        out.append(w)
    return out


class PolicyIndex:
    def __init__(self, policy_markdown: str, k1: float = 1.5, b: float = 0.75):
        self.sections = []
        pattern = re.compile(r"^## \[(?P<id>[A-Z]+-[A-Z]+-\d{2})\] (?P<title>.+)$", re.M)
        matches = list(pattern.finditer(policy_markdown))
        for i, m in enumerate(matches):
            end = matches[i + 1].start() if i + 1 < len(matches) else len(policy_markdown)
            body = " ".join(policy_markdown[m.end():end].split())
            self.sections.append({"policy_id": m["id"], "title": m["title"].strip(), "text": body})
        self.k1, self.b = k1, b
        self.docs = [_tokens(s["title"] + " " + s["title"] + " " + s["text"]) for s in self.sections]
        self.avgdl = sum(len(d) for d in self.docs) / max(len(self.docs), 1)
        df = Counter(t for d in self.docs for t in set(d))
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
        self.tf = [Counter(d) for d in self.docs]
        self.by_id = {s["policy_id"]: s for s in self.sections}

    def search(self, query: str, k: int = 3) -> list[dict]:
        q = _tokens(query)
        scored = []
        for i, tf in enumerate(self.tf):
            dl = len(self.docs[i])
            s = 0.0
            for t in q:
                if t not in tf:
                    continue
                f = tf[t]
                s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
            if s > 0:
                scored.append((s, i))
        scored.sort(reverse=True)
        return [{**self.sections[i], "score": round(s, 3)} for s, i in scored[:k]]

    def get(self, policy_id: str) -> dict | None:
        return self.by_id.get(policy_id)
