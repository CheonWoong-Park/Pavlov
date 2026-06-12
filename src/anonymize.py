"""tree-sitter-c based skeleton generator (plan §5.2).

Identifiers -> VAR_n / FUNC_n / TYPE_n / FIELD_n / LABEL_n (consistent within a
unit, numbered by first occurrence). Literals -> INT_LIT / FLOAT_LIT / STR_LIT /
CHAR_LIT. Control flow, nesting, primitive types, and operator structure are
preserved. Output remains parseable C (placeholders are valid identifiers), so
parse success can be used as a skeleton metric.

API:
  skeleton, mapping = anonymize_c(source_text)
  ok = parses_clean(text)   # tree-sitter parse with no ERROR/MISSING nodes
"""

import json
import sys

import tree_sitter_c
from tree_sitter import Language, Parser

C_LANGUAGE = Language(tree_sitter_c.language())
_parser = Parser(C_LANGUAGE)

# C keywords / primitives never rewritten (they are not `identifier` nodes for
# the most part, but type_identifier catches typedef'd names incl. these aliases)
_STD_TYPES = {
    "size_t", "ssize_t", "wchar_t", "ptrdiff_t", "intptr_t", "uintptr_t",
    "int8_t", "int16_t", "int32_t", "int64_t",
    "uint8_t", "uint16_t", "uint32_t", "uint64_t",
    "bool", "_Bool", "FILE", "va_list", "time_t", "off_t", "pid_t",
    "uchar", "ushort", "uint", "ulong",  # Ghidra primitive aliases
    "undefined", "undefined1", "undefined2", "undefined4", "undefined8",
    "byte", "word", "dword", "qword", "code",
}

_LIT_KIND = {
    "number_literal": None,  # INT_LIT or FLOAT_LIT, decided by lexeme
    "string_literal": "STR_LIT",
    "char_literal": "CHAR_LIT",
    "concatenated_string": "STR_LIT",
    "true": None, "false": None, "null": None,  # keep as-is
}


def _number_placeholder(text):
    t = text.lower().rstrip("ulf") if not text.lower().startswith("0x") else text.lower().rstrip("ul")
    if t.startswith("0x"):
        return "FLOAT_LIT" if "p" in t else "INT_LIT"  # hex float uses p-exponent
    if "." in t or "e" in t or text.lower().rstrip("ul").endswith("f"):
        return "FLOAT_LIT"
    return "INT_LIT"


class _Renamer:
    def __init__(self):
        self.maps = {"VAR": {}, "FUNC": {}, "TYPE": {}, "FIELD": {}, "LABEL": {}}

    def get(self, kind, name):
        m = self.maps[kind]
        if name not in m:
            m[name] = f"{kind}_{len(m)}"
        return m[name]


def _classify_identifier(node):
    """Return placeholder kind for an identifier-ish node, or None to keep."""
    t = node.type
    if t == "field_identifier":
        return "FIELD"
    if t == "statement_identifier":  # goto labels
        return "LABEL"
    if t == "type_identifier":
        return None if node.text.decode() in _STD_TYPES else "TYPE"
    if t != "identifier":
        return None
    p = node.parent
    if p is None:
        return "VAR"
    # function name positions
    if p.type == "function_declarator" and p.child_by_field_name("declarator") == node:
        return "FUNC"
    if p.type == "call_expression" and p.child_by_field_name("function") == node:
        return "FUNC"
    return "VAR"


def anonymize_c(source: str):
    """Return (skeleton_text, mapping dict)."""
    src = source.encode()
    tree = _parser.parse(src)
    ren = _Renamer()
    edits = []  # (start, end, replacement)

    def walk(node):
        t = node.type
        if t in ("identifier", "type_identifier", "field_identifier", "statement_identifier"):
            kind = _classify_identifier(node)
            if kind:
                edits.append((node.start_byte, node.end_byte,
                              ren.get(kind, node.text.decode())))
            return
        if t in _LIT_KIND:
            ph = _LIT_KIND[t]
            if t == "number_literal":
                ph = _number_placeholder(node.text.decode())
            if ph:
                edits.append((node.start_byte, node.end_byte, ph))
            return
        if t == "comment":
            edits.append((node.start_byte, node.end_byte, ""))
            return
        for c in node.children:
            walk(c)

    walk(tree.root_node)

    out = bytearray(src)
    for s, e, rep in sorted(edits, reverse=True):
        out[s:e] = rep.encode()
    skeleton = out.decode()
    # normalize blank lines left by comment removal
    skeleton = "\n".join(l for l in skeleton.splitlines() if l.strip())
    mapping = {k: v for k, v in ren.maps.items() if v}
    return skeleton, mapping


def parses_clean(text: str) -> bool:
    tree = _parser.parse(text.encode())
    def bad(n):
        if n.type == "ERROR" or n.is_missing:
            return True
        return any(bad(c) for c in n.children)
    return not bad(tree.root_node)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        src = open(sys.argv[1]).read()
    else:
        src = sys.stdin.read()
    skel, mapping = anonymize_c(src)
    print(skel)
    print("---MAPPING---", file=sys.stderr)
    print(json.dumps(mapping, indent=1), file=sys.stderr)
    print(f"parses_clean: {parses_clean(skel)}", file=sys.stderr)
