"""Mã SP (the broad product class of the area table) inferred from the name,
learned from the golden quotations.

Only golden rows whose sheet has a Mã SP column count (``meta.ma_sp`` is None
otherwise), and a blank code there is a real answer: golden leaves e.g. flex
ducts, canvas connectors and gaskets without a code. In order:

  1. golden rows with the same name: the code most of them carry
  2. the leading words of the name: the code golden gives names starting that
     way ("chếch ..." -> cv, "hộp gió ..." -> tb, "ống gió mềm ..." -> none),
     longest lead first, when enough rows agree
  3. the legend labels ("Cút / Co" -> cv): the label found earliest in the name

``classify`` returns a code, ``""`` when golden says the product has none, or
None when golden data has nothing to say.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

_VN = set("àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợ"
          "ùúủũụưừứửữựỳýỷỹỵđ")
_MAX_LEAD = 3      # leading words looked at
_MIN_ROWS = 3      # golden rows needed before a lead decides
_MIN_SHARE = 0.6   # share of those rows that must agree


def _norm(s: str) -> str:
    return " ".join(str(s or "").split()).lower()


def _vn_line(text: str) -> str:
    """The Vietnamese line of a multi-language name (else its first line)."""
    lines = [x.strip() for x in str(text or "").split("\n") if x.strip()]
    for x in lines:
        if any(c in _VN for c in x.lower()):
            return x
    return lines[0] if lines else ""


def _words(text: str) -> list[str]:
    return re.findall(r"[^\W\d_]+", _vn_line(text).lower())


def _pick(votes: Counter) -> str:
    """Most frequent code; a tie goes to a real code over a blank."""
    return max(votes.items(), key=lambda kv: (kv[1], kv[0] != ""))[0]


class MaSpModel:
    def __init__(self, docs, catalog: list[tuple[str, str]]):
        self.catalog = catalog
        codes = {c for _, c in catalog}
        self.by_name: dict[str, Counter] = defaultdict(Counter)
        self.by_lead: dict[tuple, Counter] = defaultdict(Counter)
        self.samples: dict[tuple, str] = {}  # (first two words, code) -> a golden name
        for d in docs:
            m = d.get("meta", {})
            if m.get("kind") or m.get("ma_sp") is None:
                continue
            code = str(m["ma_sp"]).strip().lower()
            if code and code not in codes:
                continue
            self.by_name[_norm(d["text"])][code] += 1
            w = _words(d["text"])
            for n in range(1, min(_MAX_LEAD, len(w)) + 1):
                self.by_lead[tuple(w[:n])][code] += 1
            if w and code:
                self.samples.setdefault((tuple(w[:2]), code), _vn_line(d["text"])[:70])

    def classify(self, *names: str | None) -> str | None:
        names = tuple(n for n in names if n)
        for n in names:
            votes = self.by_name.get(_norm(n))
            if votes:
                return _pick(votes)
        for n in names:
            w = _words(n)
            for k in range(min(_MAX_LEAD, len(w)), 0, -1):
                votes = self.by_lead.get(tuple(w[:k]))
                if votes and sum(votes.values()) >= _MIN_ROWS:
                    code, cnt = max(votes.items(), key=lambda kv: kv[1])
                    if cnt / sum(votes.values()) >= _MIN_SHARE:
                        return code
        return by_label(names[0], self.catalog) if names else None


def lead_supports(model: "MaSpModel", name: str | None, code: str,
                  min_rows: int = 3, min_share: float = 0.15) -> bool:
    """Golden gives ``code`` to a fair share of names starting like ``name``
    (longest lead with enough rows) — e.g. van -> tb (148 of 421 rows)."""
    w = _words(name or "")
    for k in range(min(_MAX_LEAD, len(w)), 0, -1):
        votes = model.by_lead.get(tuple(w[:k]))
        if votes and sum(votes.values()) >= _MIN_ROWS:
            return votes.get(code, 0) >= min_rows and \
                votes.get(code, 0) / sum(votes.values()) >= min_share
    return False


def by_label(name: str | None, catalog: list[tuple[str, str]]) -> str | None:
    """Legend label ("Cút / Co", "Ống bịt đầu"...) whose words all sit among
    the first words of the name; the one starting earliest wins, then the
    longest ("Cút ống gió" -> Cút / Co, "Bịt đầu ống" -> Ống bịt đầu)."""
    words = re.findall(r"[\w-]+", _vn_line(name).lower())
    best: tuple[int, int, str] | None = None
    for label, code in catalog:
        for alt in re.split(r"\s*/\s*", label.lower()):
            kw = re.findall(r"[\w-]+", alt)
            head = words[:len(kw) + 1]
            if not kw or not set(kw) <= set(head):
                continue
            key = (min(head.index(x) for x in kw), -len(kw), code)
            if best is None or key < best:
                best = key
    return best[2] if best else None


def code_examples(model: MaSpModel, per_code: int = 8, min_rows: int = 3) -> dict[str, list[str]]:
    """Real golden names illustrating each code: one per way of starting a
    name (first two words) that golden mostly codes that way, most used first
    ("cv" -> "Cút ống gió 90 độ ...", "Chếch ống gió 45 độ ...", ...)."""
    ranked: dict[str, list[tuple[int, tuple]]] = defaultdict(list)
    for lead, votes in model.by_lead.items():
        if len(lead) != min(2, len(lead)) or len(lead) == 0:
            continue
        total = sum(votes.values())
        code, cnt = max(votes.items(), key=lambda kv: kv[1])
        if code and total >= min_rows and cnt / total >= _MIN_SHARE \
                and (lead, code) in model.samples:
            ranked[code].append((cnt, lead))
    return {code: [model.samples[(lead, code)]
                   for _, lead in sorted(leads, key=lambda x: -x[0])[:per_code]]
            for code, leads in ranked.items()}
