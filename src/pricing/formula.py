"""A small Excel formula engine: parse, evaluate and re-render formulas.

Covers what Kaiyo quotations use: arithmetic (+ - * / ^ %), comparisons, text
join (&), cell refs / ranges and the functions ROUND, IF, OR, AND, NOT, SUM,
MIN, MAX, ABS, INT, SQRT, PI, LEFT, RIGHT, MID, VALUE, ROUNDUP, ROUNDDOWN.

Besides cell refs a formula may hold *variables* written ``[name]`` (e.g.
``ROUND([area]*[gia_ton]*[he_so],0)``): a price recipe expressed in item
fields, which ``render`` turns back into cell refs for a given report row.

AST nodes are tuples:
    ("num", v) ("str", s) ("bool", b) ("empty",) ("var", name)
    ("ref", sheet, col, row, abs_col, abs_row) ("range", ref, ref)
    ("func", NAME, [args]) ("bin", op, a, b) ("un", op, a) ("pct", a) ("grp", a)
"""
from __future__ import annotations

import math
import re
from decimal import ROUND_DOWN, ROUND_HALF_UP, ROUND_UP, Decimal
from typing import Any, Callable

from openpyxl.utils import column_index_from_string, get_column_letter


class FormulaError(ValueError):
    pass


_TOKEN = re.compile(r"""
    (?P<ws>\s+)
  | (?P<str>"(?:[^"]|"")*")
  | (?P<var>\[[a-z_][a-z0-9_]*\])
  | (?P<err>\#[A-Z/0!?]+[!?]?)
  | (?P<func>[A-Z][A-Z0-9.]*(?=\())
  | (?P<ref>(?:(?:'[^']+'|[A-Za-z_][\w.]*)!)?\$?[A-Z]{1,3}\$?\d+(?![\w(]))
  | (?P<bool>(?:TRUE|FALSE)(?![\w(]))
  | (?P<num>(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)
  | (?P<op><=|>=|<>|[-+*/^&=<>%(),:;])
""", re.VERBOSE)

_REF = re.compile(r"(?:(?P<sheet>'[^']+'|[A-Za-z_][\w.]*)!)?(?P<ac>\$?)(?P<col>[A-Z]{1,3})"
                  r"(?P<ar>\$?)(?P<row>\d+)")


def _tokens(text: str) -> list[tuple[str, str]]:
    out, pos = [], 0
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m:
            raise FormulaError(f"cannot tokenize at {text[pos:pos + 12]!r}")
        pos = m.end()
        kind = m.lastgroup
        if kind == "ws":
            continue
        if kind == "err":
            raise FormulaError(f"error value {m.group()}")
        out.append((kind, m.group()))
    return out


class _Parser:
    # binary operator precedence (Excel): comparison < & < +- < */ < ^
    _PREC = {"=": 1, "<>": 1, "<": 1, ">": 1, "<=": 1, ">=": 1,
             "&": 2, "+": 3, "-": 3, "*": 4, "/": 4, "^": 5}

    def __init__(self, text: str):
        self.toks = _tokens(text)
        self.i = 0

    def peek(self) -> tuple[str, str] | None:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def take(self, value: str | None = None) -> tuple[str, str]:
        tok = self.peek()
        if tok is None or (value is not None and tok[1] != value):
            raise FormulaError(f"expected {value!r}, got {tok!r}")
        self.i += 1
        return tok

    def parse(self):
        node = self.expr(0)
        if self.peek() is not None:
            raise FormulaError(f"unexpected {self.peek()!r}")
        return node

    def expr(self, min_prec: int):
        left = self.unary()
        while True:
            tok = self.peek()
            if tok is None or tok[0] != "op" or tok[1] not in self._PREC:
                return left
            prec = self._PREC[tok[1]]
            if prec < min_prec:
                return left
            self.i += 1
            # ^ is left-associative in Excel too
            right = self.expr(prec + 1)
            left = ("bin", tok[1], left, right)

    def unary(self):
        tok = self.peek()
        if tok and tok[0] == "op" and tok[1] in "+-":
            self.i += 1
            return ("un", tok[1], self.unary())
        return self.postfix()

    def postfix(self):
        node = self.primary()
        while self.peek() == ("op", "%"):
            self.i += 1
            node = ("pct", node)
        return node

    def primary(self):
        tok = self.peek()
        if tok is None:
            raise FormulaError("unexpected end")
        kind, val = tok
        self.i += 1
        if kind == "num":
            return ("num", float(val))
        if kind == "str":
            return ("str", val[1:-1].replace('""', '"'))
        if kind == "bool":
            return ("bool", val == "TRUE")
        if kind == "var":
            return ("var", val[1:-1])
        if kind == "ref":
            ref = _ref_node(val)
            if self.peek() == ("op", ":"):
                self.i += 1
                kind2, val2 = self.take()
                if kind2 != "ref":
                    raise FormulaError("bad range")
                return ("range", ref, _ref_node(val2))
            return ref
        if kind == "func":
            self.take("(")
            args = []
            if self.peek() == ("op", ")"):
                self.i += 1
                return ("func", val.upper(), args)
            while True:
                if self.peek() in (("op", ","), ("op", ";"), ("op", ")")):
                    args.append(("empty",))
                else:
                    args.append(self.expr(0))
                sep = self.take()
                if sep[1] == ")":
                    return ("func", val.upper(), args)
                if sep[1] not in ",;":
                    raise FormulaError(f"bad argument separator {sep!r}")
        if (kind, val) == ("op", "("):
            node = self.expr(0)
            self.take(")")
            return ("grp", node)
        raise FormulaError(f"unexpected {tok!r}")


def _ref_node(text: str):
    m = _REF.fullmatch(text)
    if not m:
        raise FormulaError(f"bad reference {text!r}")
    sheet = m.group("sheet")
    if sheet and sheet.startswith("'"):
        sheet = sheet[1:-1]
    return ("ref", sheet, column_index_from_string(m.group("col")), int(m.group("row")),
            bool(m.group("ac")), bool(m.group("ar")))


def parse(text: str):
    """Parse a formula (with or without the leading ``=``)."""
    text = text.strip()
    if text.startswith("="):
        text = text[1:]
    return _Parser(text).parse()


# --- evaluation -------------------------------------------------------------

def _to_num(v: Any) -> float:
    if v is None or v == "":
        return 0.0
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip().replace(",", ""))
    except ValueError:
        raise FormulaError(f"#VALUE! {v!r}") from None


def _to_str(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _to_bool(v: Any) -> bool:
    if isinstance(v, str):
        if v.upper() in ("TRUE", "FALSE"):
            return v.upper() == "TRUE"
        raise FormulaError(f"#VALUE! {v!r}")
    return bool(_to_num(v))


def _round(x: float, n: float, mode) -> float:
    q = Decimal(1).scaleb(-int(n))
    return float(Decimal(repr(x)).quantize(q, rounding=mode))


def _compare(op: str, a: Any, b: Any) -> bool:
    if isinstance(a, str) or isinstance(b, str):
        a, b = _to_str(a).lower(), _to_str(b).lower()
    else:
        a, b = _to_num(a), _to_num(b)
    return {"=": a == b, "<>": a != b, "<": a < b, ">": a > b,
            "<=": a <= b, ">=": a >= b}[op]


Resolver = Callable[[tuple], Any]


def evaluate(node, resolve: Resolver) -> Any:
    """Evaluate an AST; ``resolve`` returns the value of a ``ref`` or ``var``
    node (``None`` for a blank cell)."""
    t = node[0]
    if t in ("num", "str", "bool"):
        return node[1]
    if t == "empty":
        return None
    if t in ("ref", "var"):
        return resolve(node)
    if t == "range":
        raise FormulaError("range outside a function")
    if t == "grp":
        return evaluate(node[1], resolve)
    if t == "pct":
        return _to_num(evaluate(node[1], resolve)) / 100
    if t == "un":
        v = evaluate(node[2], resolve)
        # unary plus is a no-op in Excel, even on text ("=+B23")
        return v if node[1] == "+" else -_to_num(v)
    if t == "bin":
        op = node[1]
        a, b = evaluate(node[2], resolve), evaluate(node[3], resolve)
        if op == "&":
            return _to_str(a) + _to_str(b)
        if op in ("=", "<>", "<", ">", "<=", ">="):
            return _compare(op, a, b)
        a, b = _to_num(a), _to_num(b)
        if op == "+":
            return a + b
        if op == "-":
            return a - b
        if op == "*":
            return a * b
        if op == "/":
            if b == 0:
                raise FormulaError("#DIV/0!")
            return a / b
        if op == "^":
            return a ** b
    if t == "func":
        return _call(node[1], node[2], resolve)
    raise FormulaError(f"unknown node {t}")


def _range_values(node, resolve: Resolver) -> list[Any]:
    _, a, b = node
    if a[1] != b[1]:
        raise FormulaError("range across sheets")
    out = []
    for r in range(min(a[3], b[3]), max(a[3], b[3]) + 1):
        for c in range(min(a[2], b[2]), max(a[2], b[2]) + 1):
            out.append(resolve(("ref", a[1], c, r, False, False)))
    return out


def _call(name: str, args: list, resolve: Resolver) -> Any:
    ev = lambda i: evaluate(args[i], resolve)  # noqa: E731
    if name == "IF":
        cond = _to_bool(ev(0))
        if cond:
            return ev(1) if len(args) > 1 else True
        return ev(2) if len(args) > 2 else False
    if name in ("SUM", "MIN", "MAX", "OR", "AND"):
        vals: list[Any] = []
        for a in args:
            if a[0] == "range":
                vals.extend(v for v in _range_values(a, resolve)
                            if isinstance(v, (int, float)) and not isinstance(v, bool))
            elif a[0] != "empty":
                vals.append(evaluate(a, resolve))
        if name == "OR":
            return any(_to_bool(v) for v in vals if v is not None)
        if name == "AND":
            return all(_to_bool(v) for v in vals if v is not None)
        nums = [_to_num(v) for v in vals]
        if name == "SUM":
            return sum(nums)
        return (min if name == "MIN" else max)(nums) if nums else 0.0
    if name == "NOT":
        return not _to_bool(ev(0))
    if name == "PI":
        return math.pi
    if name == "SQRT":
        return math.sqrt(_to_num(ev(0)))
    if name == "ABS":
        return abs(_to_num(ev(0)))
    if name == "INT":
        return float(math.floor(_to_num(ev(0))))
    if name in ("ROUND", "ROUNDUP", "ROUNDDOWN"):
        mode = {"ROUND": ROUND_HALF_UP, "ROUNDUP": ROUND_UP, "ROUNDDOWN": ROUND_DOWN}[name]
        return _round(_to_num(ev(0)), _to_num(ev(1)) if len(args) > 1 else 0, mode)
    if name == "VALUE":
        return _to_num(ev(0))
    if name == "LEFT":
        return _to_str(ev(0))[:int(_to_num(ev(1))) if len(args) > 1 else 1]
    if name == "RIGHT":
        n = int(_to_num(ev(1))) if len(args) > 1 else 1
        return _to_str(ev(0))[-n:] if n else ""
    if name == "MID":
        s, start, n = _to_str(ev(0)), int(_to_num(ev(1))), int(_to_num(ev(2)))
        return s[start - 1:start - 1 + n]
    raise FormulaError(f"unsupported function {name}")


# --- inspection / rendering -----------------------------------------------------

def walk(node):
    yield node
    t = node[0]
    if t in ("grp", "pct"):
        yield from walk(node[1])
    elif t == "un":
        yield from walk(node[2])
    elif t == "bin":
        yield from walk(node[2])
        yield from walk(node[3])
    elif t == "func":
        for a in node[2]:
            yield from walk(a)
    elif t == "range":
        yield node[1]
        yield node[2]


def variables(node) -> set[str]:
    return {n[1] for n in walk(node) if n[0] == "var"}


def transform(node, fn):
    """Rebuild the tree bottom-up; ``fn(node)`` may return a replacement."""
    t = node[0]
    if t in ("grp", "pct"):
        node = (t, transform(node[1], fn))
    elif t == "un":
        node = (t, node[1], transform(node[2], fn))
    elif t == "bin":
        node = (t, node[1], transform(node[2], fn), transform(node[3], fn))
    elif t == "func":
        node = (t, node[1], [transform(a, fn) for a in node[2]])
    elif t == "range":
        node = (t, transform(node[1], fn), transform(node[2], fn))
    out = fn(node)
    return node if out is None else out


def _fmt_num(v: float) -> str:
    if float(v).is_integer():
        return str(int(v))
    return repr(float(v))


def render(node, var: Callable[[str], str] | None = None) -> str:
    """Formula text (without ``=``); ``var`` maps a variable to its text."""
    t = node[0]
    if t == "num":
        return _fmt_num(node[1])
    if t == "str":
        return '"' + node[1].replace('"', '""') + '"'
    if t == "bool":
        return "TRUE" if node[1] else "FALSE"
    if t == "empty":
        return ""
    if t == "var":
        return var(node[1]) if var else f"[{node[1]}]"
    if t == "ref":
        _, sheet, col, row, ac, ar = node
        prefix = ""
        if sheet:
            prefix = (f"'{sheet}'" if not re.fullmatch(r"[A-Za-z_][\w.]*", sheet) else sheet) + "!"
        return f"{prefix}{'$' if ac else ''}{get_column_letter(col)}{'$' if ar else ''}{row}"
    if t == "range":
        return f"{render(node[1], var)}:{render(node[2], var)}"
    if t == "grp":
        return f"({render(node[1], var)})"
    if t == "pct":
        return f"{render(node[1], var)}%"
    if t == "un":
        return f"{node[1]}{render(node[2], var)}"
    if t == "bin":
        return f"{render(node[2], var)}{node[1]}{render(node[3], var)}"
    if t == "func":
        return f"{node[1]}({','.join(render(a, var) for a in node[2])})"
    raise FormulaError(f"unknown node {t}")


def shift_rows(node, mapping: Callable[[int], int]):
    """Re-anchor every cell reference row through ``mapping``."""
    def fn(n):
        if n[0] == "ref":
            return (n[0], n[1], n[2], mapping(n[3]), n[4], n[5])
        return None
    return transform(node, fn)


def close(a: Any, b: Any) -> bool:
    """Excel-equivalent results: equal numbers (within float/rounding noise) or
    equal text (case-insensitive); blank counts as 0 / empty text."""
    if isinstance(a, str) or isinstance(b, str):
        return _to_str(a).strip().lower() == _to_str(b).strip().lower()
    try:
        x, y = _to_num(a), _to_num(b)
    except FormulaError:
        return False
    return abs(x - y) <= max(1e-6, 1e-9 * max(abs(x), abs(y)))


def canonical(node):
    """Normalise spelling without changing the result, so equivalent formulas
    from different sheets compare equal: drop a leading unary ``+`` and
    brackets around a whole argument, flatten ``*`` chains with ordered factors.
    """
    def strip(n):
        while n[0] == "grp" or (n[0] == "un" and n[1] == "+"):
            n = n[1] if n[0] == "grp" else n[2]
        return n

    def factors(n) -> list:
        # brackets may only go around a product or a single term; a sum or
        # quotient inside a product keeps them
        inner = strip(n)
        if inner[0] == "bin" and inner[1] == "*":
            return factors(inner[2]) + factors(inner[3])
        if inner[0] in ("num", "var", "ref", "func", "str", "bool"):
            return [inner]
        return [n if n[0] == "grp" or inner[0] != "bin" else ("grp", inner)]

    def fn(n):
        if n[0] == "func":
            return ("func", n[1], [strip(a) for a in n[2]])
        if n[0] == "bin" and n[1] == "*":
            fs = sorted(factors(n), key=render)
            out = fs[0]
            for f in fs[1:]:
                out = ("bin", "*", out, f)
            return out
        return None

    return strip(transform(node, fn))
