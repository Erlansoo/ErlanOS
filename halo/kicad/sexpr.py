"""Lector y escritor de S-expresiones de KiCad (esquemáticos y bibliotecas de símbolos).

Representación: listas de Python. Los tokens sin comillas son `Sym` (subclase de str),
las cadenas entre comillas son `str`, y los números son `int` o `float`.
"""
from __future__ import annotations

import re


class Sym(str):
    """Token sin comillas: palabra clave o valor simbólico (yes, no, left, passive...)."""
    __slots__ = ()


_TOKEN = re.compile(r'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)"|([^\s()"]+))', re.S)
_INT = re.compile(r"[-+]?\d+\Z")
_FLOAT = re.compile(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?\Z")
_ESCAPES = {"n": "\n", "t": "\t", '"': '"', "\\": "\\"}


def _unescape(text: str) -> str:
    if "\\" not in text:
        return text
    out, i = [], 0
    while i < len(text):
        c = text[i]
        if c == "\\" and i + 1 < len(text):
            out.append(_ESCAPES.get(text[i + 1], text[i + 1]))
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def loads(text: str):
    """Devuelve la primera expresión del texto."""
    stack: list[list] = [[]]
    end = 0
    for match in _TOKEN.finditer(text):
        end = match.end()
        opening, closing, quoted, bare = match.groups()
        if opening:
            stack.append([])
        elif closing:
            if len(stack) < 2:
                raise ValueError(f"paréntesis de cierre de más en la posición {match.start()}")
            done = stack.pop()
            stack[-1].append(done)
        elif quoted is not None:
            stack[-1].append(_unescape(quoted))
        else:
            if _INT.match(bare):
                stack[-1].append(int(bare))
            elif _FLOAT.match(bare):
                stack[-1].append(float(bare))
            else:
                stack[-1].append(Sym(bare))
    if len(stack) != 1:
        raise ValueError("faltan paréntesis de cierre")
    if text[end:].strip():
        raise ValueError("texto sobrante al final")
    if not stack[0]:
        raise ValueError("no hay expresiones")
    return stack[0][0]


def fmt_num(value: float) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return str(value)
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def _atom(value) -> str:
    if isinstance(value, Sym):
        return str(value)
    if isinstance(value, (bool, int, float)):
        return fmt_num(value)
    if isinstance(value, str):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
    raise TypeError(f"átomo no serializable: {value!r}")


def dumps(node, indent: int = 0) -> str:
    """Serializa con el estilo de KiCad: tabuladores y listas simples en una línea."""
    if not isinstance(node, list):
        return _atom(node)
    if all(not isinstance(child, list) for child in node):
        return "(" + " ".join(_atom(child) for child in node) + ")"
    tabs = "\t" * (indent + 1)
    if node and node[0] == "pts":
        points = " ".join(dumps(child) for child in node[1:])
        return "(pts\n" + tabs + points + "\n" + "\t" * indent + ")"
    lead = []
    i = 0
    while i < len(node) and not isinstance(node[i], list):
        lead.append(_atom(node[i]))
        i += 1
    out = "(" + " ".join(lead)
    for child in node[i:]:
        out += "\n" + tabs + dumps(child, indent + 1)
    return out + "\n" + "\t" * indent + ")"


def find(node, key):
    if not isinstance(node, list):
        return None
    for child in node[1:]:
        if isinstance(child, list) and child and child[0] == key:
            return child
    return None


def find_all(node, key):
    if not isinstance(node, list):
        return []
    return [c for c in node[1:] if isinstance(c, list) and c and c[0] == key]


def val(node, key, default=None, index: int = 1):
    child = find(node, key)
    if child is None or len(child) <= index:
        return default
    return child[index]


def is_yes(node, key) -> bool:
    """(key yes) / (key) / átomo suelto `key` dentro del nodo."""
    if not isinstance(node, list):
        return False
    child = find(node, key)
    if child is not None:
        return len(child) < 2 or child[1] in ("yes", True)
    return any(isinstance(c, Sym) and c == key for c in node[1:])
