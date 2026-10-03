"""Lightweight semantic search over *metadata only* (table/column names, roles, business-term synonyms).

No raw data values are embedded. Scoring = max(lexical match incl. synonyms, 0.8 * TF-IDF char-ngram cosine).
"""
from __future__ import annotations

import re
from typing import Any

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

SYNONYM_GROUPS = [
    {"revenue", "sales", "sale", "amount", "income", "turnover", "gmv", "earnings", "takings", "total", "spend"},
    {"quantity", "qty", "units", "volume", "count", "items"},
    {"price", "cost", "rate", "fee"},
    {"profit", "margin", "gain"},
    {"customer", "client", "buyer", "consumer", "account", "shopper"},
    {"product", "item", "sku", "goods", "merchandise"},
    {"region", "area", "territory", "zone", "geography", "location", "state", "market"},
    {"category", "type", "class", "department", "group", "line"},
    {"segment", "tier", "cohort"},
    {"date", "time", "period", "day", "month", "year", "when"},
    {"order", "transaction", "purchase", "invoice", "booking"},
    {"discount", "rebate", "promo", "promotion", "markdown"},
    {"city", "town"},
]
GENERIC_MODIFIERS = {"unit"}   # "units" means quantity, not unit_price
STOP = {"the", "a", "an", "of", "by", "per", "in", "for", "to", "and", "is", "are", "was", "what", "which", "show", "me", "my"}


def tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t and t not in STOP]


def stem(tok: str) -> str:
    for suf, rep in (("ies", "y"), ("sses", "ss"), ("es", ""), ("s", "")):
        if tok.endswith(suf) and len(tok) > len(suf) + 2:
            return tok[: -len(suf)] + rep
    return tok


def expand(term: str) -> set[str]:
    """A term plus every synonym in its business-concept group (stemmed)."""
    t = stem(term)
    out = {t}
    for g in SYNONYM_GROUPS:
        if t in {stem(x) for x in g}:
            out |= {stem(x) for x in g}
    return out


class SemanticIndex:
    def __init__(self, schemas: dict[str, list[dict[str, Any]]]):
        self.entries: list[dict[str, Any]] = []
        docs: list[str] = []
        for table, cols in schemas.items():
            self.entries.append({"table": table, "column": None, "role": "table", "tokens": {stem(t) for t in tokens(table)}})
            docs.append(" ".join(tokens(table)) + " table dataset")
            for c in cols:
                toks = {stem(t) for t in tokens(c["name"])}
                if len(toks) > 1:
                    toks -= GENERIC_MODIFIERS
                syn = set().union(*(expand(t) for t in toks)) if toks else set()
                self.entries.append({"table": table, "column": c["name"], "role": c["role"], "kind": c["kind"],
                                     "tokens": toks, "synonyms": syn, "is_money": c.get("is_money", False)})
                docs.append(" ".join(sorted(toks | syn)) + f" {c['role']} {table}")
        self.vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 4)).fit(docs) if docs else None
        self.matrix = self.vec.transform(docs) if docs else None

    def search(self, query: str, top_k: int = 5, roles: set[str] | None = None) -> list[dict[str, Any]]:
        q_tokens = {stem(t) for t in tokens(query)}
        if not q_tokens or not self.entries:
            return []
        q_exp = set().union(*(expand(t) for t in q_tokens))
        sims = cosine_similarity(self.vec.transform([" ".join(sorted(q_exp))]), self.matrix)[0]
        results = []
        for e, sim in zip(self.entries, sims):
            if e["column"] is None or (roles and e["role"] not in roles):
                continue
            direct = len(q_tokens & e["tokens"]) / max(len(e["tokens"]), 1)
            synonym = 0.85 if (q_exp & e["tokens"]) else 0.0
            score = max(direct, synonym, 0.8 * float(sim))
            if q_tokens & e["tokens"]:
                score += 0.1  # exact stem match beats a mere synonym (price -> unit_price, not unit_cost)
            if e.get("is_money") and q_exp & {stem("revenue")}:
                score += 0.05
            if e["role"] == "id":
                score -= 0.03  # prefer meaningful columns over keys on ties
            if score > 0.2:
                results.append({"table": e["table"], "column": e["column"], "role": e["role"], "score": round(score, 3)})
        results.sort(key=lambda r: -r["score"])
        return results[:top_k]
