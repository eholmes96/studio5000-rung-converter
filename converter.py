#!/usr/bin/env python3
"""
Studio 5000 / Logix Designer  --  Neutral Text <-> ASCII rung converter.

Single-file Tkinter app.  No external dependencies, no network access, no telemetry.

    python converter.py              # launch the GUI
    python converter.py --selftest   # run the round-trip self-test, exit 0/1

Formats
-------
Neutral text   XIC(a)XIO(b)OTE(c);          rungs terminated by ';', branches [leg,leg]
ASCII          SOR XIC a XIO b OTE c EOR    space-delimited, branches BST/NXB/BND

Both formats are emitted as ONE line with no newline characters, because Studio
treats every newline in pasted ASCII as a rung break.

Grounded in:
  * Logix 5000 Controllers Import/Export Reference Manual, 1756-RM014D-EN-P (Sept 2025),
    Chapter 9 "Define a ladder logic routine" -- branch examples and the
    "Neutral text for ladder instructions" table (pp. 210-221).
  * Control-character escape table, 1756-RM014D-EN-P p. 40.
"""

from __future__ import annotations

import bisect
import json
import os
import re
import sys
from dataclasses import dataclass, field

APP_TITLE = "Studio 5000 Neutral <-> ASCII Rung Converter"

# ---------------------------------------------------------------------------
# USER-EDITABLE INSTRUCTION SET
# ---------------------------------------------------------------------------
# Seeded from the "Neutral text for ladder instructions" table in the Logix 5000
# Controllers Import/Export Reference Manual, publication 1756-RM014D-EN-P
# (September 2025), pages 210-221.  That table is the authoritative list of relay
# ladder instructions and their neutral-text formats; it spans the general
# instructions (1756-RM003 / 1756-RM018), the process & drives instructions
# (1756-RM006), the safety instructions, and the motion instructions
# (MOTION-RM002, formerly 1756-RM007).
#
# This set is used ONLY for the ASCII -> Neutral direction, to tell a mnemonic
# apart from an operand.  Neutral -> ASCII never consults it.
#
# >>> ADD YOUR ADD-ON INSTRUCTION (AOI) NAMES TO AOI_INSTRUCTIONS BELOW. <<<
KNOWN_INSTRUCTIONS = [
    "ABL", "ABS", "ACB", "ACL", "ACS", "ADD", "AFI", "AHL", "ALMA", "ALMD", "AND", "ARD",
    "ARL", "ASN", "ATN", "AVC", "AVE", "AWA", "AWT", "BRK", "BSL", "BSR", "BTD", "CBCM",
    "CBIM", "CBSSM", "CLR", "CMP", "CONCAT", "COP", "COS", "CPM", "CPS", "CPT", "CROUT",
    "CSM", "CTD", "CTU", "DCM", "DCS", "DCSRT", "DCST", "DCSTL", "DCSTM", "DDT", "DEG",
    "DELETE", "DIN", "DIV", "DTOS", "DTR", "ENPEN", "EOT", "EPMS", "EQU", "ESTOP", "EVENT",
    "FAL", "FBC", "FFL", "FFU", "FIND", "FLL", "FOR", "FPMS", "FRD", "FSBM", "FSC", "GEQ",
    "GRT", "GSV", "INSERT", "IOT", "JMP", "JSR", "JXR", "LBL", "LC", "LEQ", "LES", "LFL",
    "LFU", "LIM", "LN", "LOG", "LOWER", "MAAT", "MAFR", "MAG", "MAH", "MAHD", "MAJ", "MAM",
    "MAOC", "MAPC", "MAR", "MAS", "MASD", "MASR", "MATC", "MAW", "MCCD", "MCCM", "MCCP",
    "MCD", "MCLM", "MCR", "MCS", "MCSD", "MCSR", "MCSV", "MCT", "MCTP", "MDF", "MDO",
    "MDOC", "MDR", "MDW", "MEQ", "MGS", "MGSD", "MGSP", "MGSR", "MID", "MMVC", "MOD", "MOV",
    "MRAT", "MRHD", "MRP", "MSF", "MSG", "MSO", "MUL", "MVC", "MVM", "NEG", "NEQ", "NOP",
    "NOT", "ONS", "OR", "OSF", "OSR", "OTE", "OTL", "OTU", "PATT", "PCLF", "PCMD", "PDET",
    "PFL", "PID", "POVR", "PPD", "PRNP", "PSC", "PXRQ", "RAD", "RES", "RET", "RIN", "ROUT",
    "RTO", "RTOS", "SBR", "SFP", "SFR", "SIN", "SIZE", "SMAT", "SQI", "SQL", "SQO", "SQR",
    "SRT", "SSV", "STD", "STOD", "STOR", "SUB", "SWPB", "TAN", "THRS", "THRSE", "TND", "TOD",
    "TOF", "TON", "TRN", "TSAM", "TSSM", "UID", "UIE", "UPPER", "XIC", "XIO", "XOR", "XPY",
]

# Add your project's Add-On Instructions here. Anything listed is treated as a
# mnemonic with no warning.
#
# This matters ONLY for ASCII -> Neutral. An AOI name shaped like a tag -- with
# underscores and lowercase, e.g. Example_Aoi_1 -- is otherwise read as an operand
# and folded into the preceding instruction, silently and with no warning. Add
# every AOI your routines call and that stops happening.
#
# Left empty by default: the tool ships knowing only the standard Rockwell
# instruction set, so no site-specific AOI names live in this file.
AOI_INSTRUCTIONS: list[str] = [
]

# ---------------------------------------------------------------------------
# Logix Designer v36 mnemonic changes
# ---------------------------------------------------------------------------
# v36 renamed 16 instructions to conform to IEC 61131-3 and PLCopen. Studio v36+
# emits the NEW spelling when you copy rungs; older projects and exports carry
# the legacy one. Both are accepted here, and neither is ever rewritten -- the
# converter reproduces whichever spelling you gave it.
#
# Source: Studio 5000 Logix Designer online help, Instruction Set >
# "Updated instruction mnemonics" (v37 docset), which tabulates
# "Mnemonic in versions v35 and earlier" against "Mnemonic in versions v36 and later".
#
# Note: the 1756-RM014D import/export manual (Sept 2025) still lists only the
# legacy spellings in its neutral-text table, so KNOWN_INSTRUCTIONS above -- which
# is extracted from that table -- does not include these. See the README.
V36_MNEMONIC_UPDATES = {
    "ACS": "ACOS",      # Arc cosine
    "ASN": "ASIN",      # Arc sine
    "ATN": "ATAN",      # Arc tangent
    "TOD": "TO_BCD",    # Convert to BCD
    "FRD": "BCD_TO",    # Convert to integer
    "EQU": "EQ",        # Equal to
    "GRT": "GT",        # Greater than
    "GEQ": "GE",        # Greater than or equal to
    "LEQ": "LE",        # Less than or equal to
    "LES": "LT",        # Less than
    "LIM": "LIMIT",     # Limit
    "MOV": "MOVE",      # Move
    "NEQ": "NE",        # Not equal to
    "SQR": "SQRT",      # Square root
    "TRN": "TRUNC",     # Truncate
    "XPY": "EXPT",      # X to the power of Y
}

INSTRUCTION_SET = (set(KNOWN_INSTRUCTIONS)
                   | set(V36_MNEMONIC_UPDATES.values())
                   | set(AOI_INSTRUCTIONS))

# Instructions whose last operand is a free-form *expression* that may contain
# spaces, e.g. CPT(dest,ValueA / (ValueB *100)).  Because ASCII is space-delimited,
# Studio wraps such an operand in double quotes:
#
#     CPT Drive1.Status.Current "_Drive1:I.OutputCurrent / (Drive1.Config.MotorFLA *100)"
#
# The value below is the number of ordinary operands that come BEFORE the
# expression, taken from the "Neutral text for ladder instructions" table in
# 1756-RM014D-EN-P pp. 210-221:
#
#     CMP(expression)                                        -> 0
#     CPT(destination,expression)                            -> 1
#     FSC(control,length,position,mode,expression)           -> 4
#     FAL(control,length,position,mode,destination,expression) -> 5
#
# It is used only to re-assemble an expression that arrives UNQUOTED in ASCII
# input. Correctly quoted input needs no arity knowledge at all.
EXPRESSION_OPERAND_INDEX = {
    "CMP": 0,
    "CPT": 1,
    "FSC": 4,
    "FAL": 5,
}

# ---------------------------------------------------------------------------
# Structural tokens
# ---------------------------------------------------------------------------
TOK_SOR, TOK_EOR = "SOR", "EOR"
TOK_BST, TOK_NXB, TOK_BND = "BST", "NXB", "BND"
STRUCTURAL = {TOK_SOR, TOK_EOR, TOK_BST, TOK_NXB, TOK_BND}

# ---------------------------------------------------------------------------
# Syntax highlighting
# ---------------------------------------------------------------------------
# Token categories the panes colour. Tags and ordinary operands are deliberately
# NOT a category -- they render in the pane's normal text colour, which keeps a
# dense routine readable. Every colour here is user-configurable at runtime via
# View > Syntax colours...
TAG_RUNG = "rung"
TAG_BRANCH = "branch"
TAG_INSTRUCTION = "instruction"
TAG_AOI = "aoi"
TAG_LITERAL = "literal"
TAG_EXPRESSION = "expression"
TAG_COMMENT = "comment"

SYNTAX_CATEGORIES = (TAG_RUNG, TAG_BRANCH, TAG_INSTRUCTION, TAG_AOI,
                     TAG_LITERAL, TAG_EXPRESSION, TAG_COMMENT)

# Not a syntax category: a background tag marking the other occurrences of the
# current find term, so the active (blue) match stays distinguishable from them.
TAG_FIND_ALL = "find_all"

DEFAULT_SYNTAX_COLORS = {
    TAG_RUNG: "#5F5E5A",         # SOR EOR ;
    TAG_BRANCH: "#534AB7",       # BST NXB BND [ , ]
    TAG_INSTRUCTION: "#185FA5",  # XIC OTE CPT ... including compute
    TAG_AOI: "#A32D2D",          # mnemonic not in the known set
    TAG_LITERAL: "#3B6D11",      # 5000, 16#000F, 'text'
    TAG_EXPRESSION: "#854F0B",   # the CPT/CMP/FAL/FSC expression operand
    TAG_COMMENT: "#1B5E20",      # // rung comment lines (dark green)
}

SYNTAX_LABELS = {
    TAG_RUNG: "Rung",
    TAG_BRANCH: "Branch",
    TAG_INSTRUCTION: "Instruction",
    TAG_AOI: "AOI / unknown",
    TAG_LITERAL: "Raw value",
    TAG_EXPRESSION: "Quoted expression",
    TAG_COMMENT: "Rung comment",
}

SYNTAX_HINTS = {
    TAG_RUNG: "SOR   EOR   ;",
    TAG_BRANCH: "BST   NXB   BND   [  ,  ]",
    TAG_INSTRUCTION: "XIC   OTE   TON   CPT",
    TAG_AOI: "mnemonic not in the known set",
    TAG_LITERAL: "5000   16#000F   'text'",
    TAG_EXPRESSION: "the CPT / CMP / FAL / FSC expression",
    TAG_COMMENT: "// a line above its rung",
}

# ---------------------------------------------------------------------------
# Rung comments
# ---------------------------------------------------------------------------
# Studio's plain-text copy carries no comments, but its rung scrap file (.shs,
# see the "Studio rung scrap files" section below) does. In the panes a comment
# is shown as lines starting with "//" directly ABOVE the rung it belongs to.
# Only a whole line counts: "//" after logic on the same line is not a comment.
# A comment belongs to the rung that the next piece of logic after it is part of.
COMMENT_PREFIX = "//"
COMMENT_LINE = re.compile(r"(?m)^[ \t]*//.*$")


def _comment_starts_here(text: str, i: int) -> bool:
    """True if text[i:] begins a comment line: '//' with only spaces/tabs before
    it on its line."""
    if not text.startswith(COMMENT_PREFIX, i):
        return False
    j = i - 1
    while j >= 0 and text[j] in " \t":
        j -= 1
    return j < 0 or text[j] == "\n"


def _scan_comment_line(text: str, i: int) -> tuple[str, int]:
    """Read the comment line starting at text[i] == '/'. Returns the comment text
    (prefix and one following space removed, trailing CR dropped) and the index
    of the line's terminating newline (or len(text))."""
    end = text.find("\n", i)
    if end < 0:
        end = len(text)
    body = text[i + len(COMMENT_PREFIX):end].rstrip("\r")
    if body.startswith(" "):
        body = body[1:]
    return body, end


def comment_display_lines(comment: str) -> list[str]:
    """Pane lines for one rung comment: '// ' before every line of it."""
    return [(COMMENT_PREFIX + " " + line) if line else COMMENT_PREFIX
            for line in comment.split("\n")]


def _with_comments(lines: list[str], comments: list[str | None]) -> list[str]:
    """Interleave each rung's comment lines above the rung's own line."""
    out: list[str] = []
    for line, comment in zip(lines, comments):
        if comment is not None:
            out.extend(comment_display_lines(comment))
        out.append(line)
    return out

# A raw value: decimal, float, exponent, or a radix-prefixed Logix literal
# (2#0000_1111, 8#017, 16#000F). Quoted strings are handled separately.
LITERAL_SHAPE = re.compile(
    r"^[-+]?(?:\d+#[0-9A-Fa-f_]+|\d+(?:\.\d*)?(?:[eE][-+]?\d+)?|\.\d+)$"
)


def _is_literal(operand: str) -> bool:
    return bool(operand) and (operand.startswith("'")
                              or bool(LITERAL_SHAPE.match(operand)))


def _operand_category(mnemonic: str, index: int, operand: str) -> str | None:
    """Highlight category for one operand, or None to leave it as plain text."""
    expr_at = EXPRESSION_OPERAND_INDEX.get(mnemonic)
    if expr_at is not None and index >= expr_at:
        return TAG_EXPRESSION
    if _is_literal(operand):
        return TAG_LITERAL
    return None


# Characters that end a "word" for double-click selection.
#
# Tcl on Windows defines a word as any run of non-whitespace, so double-clicking
# anywhere in neutral text -- which has no spaces at all -- selects the entire
# rung. These delimiters make a double-click grab one operand or mnemonic instead.
#
# '.' and ':' are deliberately NOT delimiters: a dotted member path and a module
# address are each one thing you want to grab whole, e.g. selecting anywhere in
# _Drive1:I.OutputCurrent should select all of it.
WORD_DELIMITERS = frozenset(" \t\r\n()[],;\"'")

# A mnemonic looks like a short all-caps token, optionally with a trailing digit.
MNEMONIC_SHAPE = re.compile(r"^[A-Z]{2,4}[0-9]?$")
ASCII_KEYWORD = re.compile(r"\b(?:SOR|EOR|BST|NXB|BND)\b")
NEUTRAL_CALL = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\s*\(")


# ---------------------------------------------------------------------------
# Limits and errors
# ---------------------------------------------------------------------------
# The emitters recurse once per branch level, so unbounded nesting would hit
# Python's recursion limit (~994 here) and raise deep inside a conversion. Real
# ladder never approaches this -- Studio's own nesting limit is far lower -- so
# anything past the cap means the input is not ladder text. Refusing loudly beats
# crashing, and beats emitting something structurally different from the input.
MAX_BRANCH_DEPTH = 100


class ConversionError(Exception):
    """Input cannot be converted safely. Carries a message fit to show the user."""


# ---------------------------------------------------------------------------
# Rung model
# ---------------------------------------------------------------------------
@dataclass
class Instruction:
    mnemonic: str
    operands: list[str] = field(default_factory=list)


@dataclass
class Branch:
    legs: list[list] = field(default_factory=lambda: [[]])


@dataclass
class Raw:
    """Unrecognized non-logic text, passed through untouched with a soft warning."""
    text: str


# ---------------------------------------------------------------------------
# Neutral text parsing
# ---------------------------------------------------------------------------
def _scan_string_literal(text: str, i: int) -> tuple[str, int]:
    """Scan a Logix single-quoted string starting at text[i] == "'".

    Inside a string, '$' escapes the next character (1756-RM014D-EN-P p. 40:
    $$ = literal $, $' = quote, $Q, $L, $N, $P, $R, $T, and $xxxx hex).
    Returns the literal including both quotes, and the index just past it.
    """
    out = [text[i]]
    i += 1
    n = len(text)
    while i < n:
        c = text[i]
        if c == "$" and i + 1 < n:
            out.append(c)
            out.append(text[i + 1])
            i += 2
            continue
        out.append(c)
        i += 1
        if c == "'":
            break
    return "".join(out), i


def _scan_operands(text: str, i: int) -> tuple[list[str], list[tuple[int, int]], int]:
    """Scan an operand list. text[i] must be '('.

    Returns (operands, positions, index past ')'), where positions[k] is the
    (start, end) offset of operands[k] in `text`, whitespace excluded, for
    syntax highlighting.

    Operand separators are commas at paren-depth 1 AND bracket-depth 0, so that
    multi-dimensional array subscripts (Tag[1,2]), nested expressions
    (CPT(dst,(a+b)*c)) and string literals ('a,b') are preserved verbatim.
    """
    n = len(text)
    depth = 0        # parenthesis depth
    bdepth = 0       # bracket depth, within the current operand
    raw: list[tuple[str, int]] = []   # (text, start offset) per operand
    buf: list[str] = []
    start = i + 1
    while i < n:
        c = text[i]
        if c == "'":
            lit, i = _scan_string_literal(text, i)
            buf.append(lit)
            continue
        if c == "(":
            depth += 1
            if depth > 1:
                buf.append(c)
            else:
                start = i + 1
            i += 1
            continue
        if c == ")":
            depth -= 1
            if depth == 0:
                raw.append(("".join(buf), start))
                i += 1
                break
            buf.append(c)
            i += 1
            continue
        if c == "[":
            bdepth += 1
            buf.append(c)
            i += 1
            continue
        if c == "]":
            bdepth = max(0, bdepth - 1)
            buf.append(c)
            i += 1
            continue
        if c == "," and depth == 1 and bdepth == 0:
            raw.append(("".join(buf), start))
            buf = []
            i += 1
            start = i
            continue
        buf.append(c)
        i += 1
    else:
        # Ran off the end without a closing paren.
        raw.append(("".join(buf), start))

    operands: list[str] = []
    positions: list[tuple[int, int]] = []
    for chunk, chunk_start in raw:
        stripped = chunk.strip()
        lead = len(chunk) - len(chunk.lstrip())
        operands.append(stripped)
        positions.append((chunk_start + lead, chunk_start + lead + len(stripped)))

    if len(operands) == 1 and operands[0] == "":
        operands, positions = [], []    # MNEMONIC() -> zero operands
    return operands, positions, i


def parse_neutral(text: str) -> tuple[list[list], list[str]]:
    rungs, warnings, _, _ = _parse_neutral(text)
    return rungs, warnings


def _parse_neutral(text: str) -> tuple[list[list], list[str], list[tuple[int, int, str]],
                                       list[str | None]]:
    """Parse neutral text into a list of rungs. Stack-based; newlines are whitespace.

    Also returns highlight spans as (start, end, category) offsets into `text`, and
    one comment per rung (None where the rung has none) from '//' lines.
    """
    warnings: list[str] = []
    rungs: list[list] = []
    spans: list[tuple[int, int, str]] = []
    pending: list[str] = []                  # comment lines not yet given a rung
    comments: dict[int, list[str]] = {}

    def attach_comment() -> None:
        # Logic is about to land in rung number len(rungs): it owns the comment.
        if pending:
            comments.setdefault(len(rungs), []).extend(pending)
            pending.clear()

    rung: list = []
    branch_stack: list[Branch] = []
    target_stack: list[list] = [rung]   # where the next element is appended

    def end_rung() -> None:
        nonlocal rung, target_stack
        if branch_stack:
            warnings.append("Unclosed branch '[' before end of rung - auto-closed.")
            branch_stack.clear()
        rungs.append(rung)
        rung = []
        target_stack = [rung]

    i, n = 0, len(text)
    stray: list[str] = []

    def flush_stray() -> None:
        if stray:
            blob = "".join(stray).strip()
            if blob:
                target_stack[-1].append(Raw(blob))
                warnings.append(f"Unrecognized text passed through: {blob[:60]!r}")
            stray.clear()

    while i < n:
        c = text[i]

        if c.isspace():
            i += 1
            continue

        if c == "/" and _comment_starts_here(text, i):
            flush_stray()
            body, end = _scan_comment_line(text, i)
            spans.append((i, end, TAG_COMMENT))
            pending.append(body)
            i = end
            continue

        attach_comment()

        if c == "[":
            flush_stray()
            if len(branch_stack) >= MAX_BRANCH_DEPTH:
                raise ConversionError(
                    f"Branch nesting goes deeper than {MAX_BRANCH_DEPTH} levels "
                    f"(at character {i:,}). Studio never produces ladder this deeply "
                    f"nested, so this usually means the input is not rung text."
                )
            spans.append((i, i + 1, TAG_BRANCH))
            br = Branch(legs=[[]])
            target_stack[-1].append(br)
            branch_stack.append(br)
            target_stack.append(br.legs[0])
            i += 1
            continue

        if c == ",":
            flush_stray()
            if branch_stack:
                spans.append((i, i + 1, TAG_BRANCH))
                br = branch_stack[-1]
                br.legs.append([])
                target_stack[-1] = br.legs[-1]
            else:
                warnings.append("Comma outside a branch - passed through.")
                stray.append(c)
            i += 1
            continue

        if c == "]":
            flush_stray()
            if branch_stack:
                spans.append((i, i + 1, TAG_BRANCH))
                branch_stack.pop()
                target_stack.pop()
            else:
                warnings.append("Unmatched ']' - ignored.")
            i += 1
            continue

        if c == ";":
            flush_stray()
            spans.append((i, i + 1, TAG_RUNG))
            end_rung()
            i += 1
            continue

        if c.isalnum() or c == "_":
            j = i
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            mnemonic = text[i:j]
            k = j
            while k < n and text[k].isspace():
                k += 1
            if k < n and text[k] == "(":
                # RM014 shows some instructions with a space before '(' e.g. "ALMA (...)".
                operands, positions, end = _scan_operands(text, k)
                flush_stray()
                spans.append((i, j, TAG_INSTRUCTION if mnemonic in INSTRUCTION_SET
                              else TAG_AOI))
                for idx, (operand, (op_start, op_end)) in enumerate(
                        zip(operands, positions)):
                    category = _operand_category(mnemonic, idx, operand)
                    if category:
                        spans.append((op_start, op_end, category))
                i = end
                target_stack[-1].append(Instruction(mnemonic, operands))
            else:
                warnings.append(
                    f"'{mnemonic}' has no operand list in neutral text - treated as "
                    f"a zero-operand instruction."
                )
                flush_stray()
                spans.append((i, j, TAG_INSTRUCTION if mnemonic in INSTRUCTION_SET
                              else TAG_AOI))
                target_stack[-1].append(Instruction(mnemonic, []))
                i = j
            continue

        stray.append(c)
        i += 1

    flush_stray()
    if rung:
        warnings.append("Last rung had no terminating ';' - added.")
        end_rung()
    if pending:
        warnings.append(_ORPHAN_COMMENT_WARNING)

    rung_comments = _comment_list(comments, len(rungs))
    _strip_trailing_empty_rung(rungs, rung_comments)
    return rungs, warnings, spans, rung_comments


# ---------------------------------------------------------------------------
# ASCII parsing
# ---------------------------------------------------------------------------
def classify_token(tok: str) -> bool:
    """True if tok should be read as a mnemonic, False if it is an operand.

    The built-in instruction set is authoritative; the shape heuristic is only a
    fallback for tokens not in it (i.e. Add-On Instructions).
    """
    if tok in INSTRUCTION_SET:
        return True
    return bool(MNEMONIC_SHAPE.match(tok))


def _tokenize_ascii(text: str, warnings: list[str]) -> list[tuple[str, str, int, int]]:
    """Split ASCII text into (kind, token, start, end) tuples, kind being "word",
    "quoted" or "comment".

    A double-quoted run is one token no matter how much whitespace it contains,
    which is how an expression operand such as
    "_Drive1:I.OutputCurrent / (Drive1.Config.MotorFLA *100)" survives. A quoted token
    is always an operand and is never classified as a mnemonic. Offsets span the
    quotes as well, so highlighting covers the whole literal. A "comment" token is
    a whole '//' line; its text has the prefix removed.
    """
    tokens: list[tuple[str, str, int, int]] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
            continue
        if c == "/" and _comment_starts_here(text, i):
            body, end = _scan_comment_line(text, i)
            tokens.append(("comment", body, i, end))
            i = end
            continue
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 1
            if j >= n:
                warnings.append("Unterminated double quote - text to end of input "
                                "was read as one operand.")
                tokens.append(("quoted", text[i + 1:], i, n))
                break
            tokens.append(("quoted", text[i + 1:j], i, j + 1))
            i = j + 1
            continue
        j = i
        while j < n and not text[j].isspace():
            j += 1
        tokens.append(("word", text[i:j], i, j))
        i = j
    return tokens


def _rejoin_expression_operands(elements: list, warnings: list[str]) -> None:
    """Repair an expression operand that arrived in ASCII without quotes.

    An unquoted `CPT dest A / (B *100)` tokenizes into four operands. For the
    instructions in EXPRESSION_OPERAND_INDEX the expression is always the final
    parameter, so any surplus operands belong to it and are re-joined.
    """
    for el in elements:
        if isinstance(el, Branch):
            for leg in el.legs:
                _rejoin_expression_operands(leg, warnings)
        elif isinstance(el, Instruction):
            idx = EXPRESSION_OPERAND_INDEX.get(el.mnemonic)
            if idx is not None and len(el.operands) > idx + 1:
                el.operands[idx:] = [" ".join(el.operands[idx:])]
                warnings.append(
                    f"{el.mnemonic} expression was not quoted in the ASCII input; "
                    f"re-joined it into one operand. Runs of whitespace inside the "
                    f"expression are collapsed to single spaces."
                )


def parse_ascii(text: str) -> tuple[list[list], list[str]]:
    rungs, warnings, _, _ = _parse_ascii(text)
    return rungs, warnings


def _parse_ascii(text: str) -> tuple[list[list], list[str], list[tuple[int, int, str]],
                                     list[str | None]]:
    """Parse ASCII text into a list of rungs.

    Rung boundaries come only from tokens, never from newlines: a rung ends at
    EOR or at the next SOR, whichever comes first.

    Also returns highlight spans as (start, end, category) offsets into `text`, and
    one comment per rung (None where the rung has none) from '//' lines.
    """
    warnings: list[str] = []
    unknown: list[str] = []
    rungs: list[list] = []
    spans: list[tuple[int, int, str]] = []
    pending: list[str] = []                  # comment lines not yet given a rung
    comments: dict[int, list[str]] = {}

    def attach_comment() -> None:
        # The open rung is not in `rungs` yet, so its number is len(rungs).
        if pending:
            comments.setdefault(len(rungs), []).extend(pending)
            pending.clear()

    rung: list | None = None
    branch_stack: list[Branch] = []
    target_stack: list[list] = []
    current: Instruction | None = None
    # Operands the current instruction is still guaranteed to take before its
    # expression. While this is non-zero, a mnemonic-shaped token such as the
    # FAL/FSC mode operand ALL or INC must be read as an operand, not as an
    # instruction.
    forced_operands = 0

    def open_rung() -> None:
        nonlocal rung, target_stack, current, forced_operands
        if rung is None:
            rung = []
            target_stack = [rung]
            branch_stack.clear()
            current = None
            forced_operands = 0

    def close_rung() -> None:
        nonlocal rung, target_stack, current, forced_operands
        if rung is not None:
            if branch_stack:
                warnings.append("Unclosed BST before end of rung - auto-closed.")
                branch_stack.clear()
            rungs.append(rung)
            rung = None
            target_stack = []
            current = None
            forced_operands = 0

    for kind, tok, start, end in _tokenize_ascii(text, warnings):
        if kind == "comment":
            spans.append((start, end, TAG_COMMENT))
            pending.append(tok)
            continue

        if kind == "quoted":
            # A quoted token is an operand by definition, never a mnemonic.
            open_rung()
            attach_comment()
            spans.append((start, end, TAG_EXPRESSION))
            if current is None:
                warnings.append(f"Quoted operand {tok!r} had no preceding instruction "
                                f"- passed through.")
                target_stack[-1].append(Raw(tok))
            else:
                current.operands.append(tok)
                forced_operands = 0
            continue

        if tok == TOK_SOR:
            close_rung()
            open_rung()
            attach_comment()
            current = None
            forced_operands = 0
            spans.append((start, end, TAG_RUNG))
            continue

        if tok == TOK_EOR:
            open_rung()
            attach_comment()
            close_rung()
            spans.append((start, end, TAG_RUNG))
            continue

        open_rung()
        attach_comment()

        if tok == TOK_BST:
            current = None
            forced_operands = 0
            if len(branch_stack) >= MAX_BRANCH_DEPTH:
                raise ConversionError(
                    f"BST nesting goes deeper than {MAX_BRANCH_DEPTH} levels "
                    f"(at character {start:,}). Studio never produces ladder this "
                    f"deeply nested, so this usually means the input is not rung text."
                )
            spans.append((start, end, TAG_BRANCH))
            br = Branch(legs=[[]])
            target_stack[-1].append(br)
            branch_stack.append(br)
            target_stack.append(br.legs[0])
            continue

        if tok == TOK_NXB:
            current = None
            forced_operands = 0
            spans.append((start, end, TAG_BRANCH))
            if branch_stack:
                br = branch_stack[-1]
                br.legs.append([])
                target_stack[-1] = br.legs[-1]
            else:
                warnings.append("NXB outside a branch - ignored.")
            continue

        if tok == TOK_BND:
            current = None
            forced_operands = 0
            spans.append((start, end, TAG_BRANCH))
            if branch_stack:
                branch_stack.pop()
                target_stack.pop()
            else:
                warnings.append("Unmatched BND - ignored.")
            continue

        # A token inside an instruction's guaranteed operand prefix is an operand
        # even when it is shaped like a mnemonic (FAL/FSC modes ALL, INC, ...).
        is_mnemonic = classify_token(tok) if forced_operands == 0 else False

        if is_mnemonic:
            current = Instruction(tok, [])
            target_stack[-1].append(current)
            forced_operands = EXPRESSION_OPERAND_INDEX.get(tok, 0)
            known = tok in INSTRUCTION_SET
            spans.append((start, end, TAG_INSTRUCTION if known else TAG_AOI))
            if not known:
                unknown.append(tok)
        else:
            if current is None:
                # An operand-shaped token where an instruction was expected.
                # Keep it rather than drop it, and flag it.
                current = Instruction(tok, [])
                target_stack[-1].append(current)
                forced_operands = 0
                spans.append((start, end, TAG_AOI))
                unknown.append(tok)
            else:
                category = _operand_category(current.mnemonic,
                                             len(current.operands), tok)
                if category:
                    spans.append((start, end, category))
                current.operands.append(tok)
                if forced_operands:
                    forced_operands -= 1

    close_rung()
    if pending:
        warnings.append(_ORPHAN_COMMENT_WARNING)
    rung_comments = _comment_list(comments, len(rungs))
    _strip_trailing_empty_rung(rungs, rung_comments)

    for r in rungs:
        _rejoin_expression_operands(r, warnings)
    warnings[:] = list(dict.fromkeys(warnings))

    if unknown:
        seen = list(dict.fromkeys(unknown))
        warnings.append(
            "Unrecognized instruction(s): " + ", ".join(seen) + " - verify output."
        )
    return rungs, warnings, spans, rung_comments


def highlight_spans(text: str, kind: str) -> list[tuple[int, int, int, int, str]]:
    """Syntax-highlight `text`, returning (line, start_col, end_col, ...) spans.

    Lines are 1-based to match Tk text indices. Works on raw pane content, so the
    input pane highlights correctly even when it has not been reformatted.
    """
    if kind == "Neutral":
        _, _, spans, _ = _parse_neutral(text)
    elif kind == "ASCII":
        _, _, spans, _ = _parse_ascii(text)
    else:
        return []

    line_starts = [0]
    for idx, ch in enumerate(text):
        if ch == "\n":
            line_starts.append(idx + 1)

    out = []
    for start, end, category in spans:
        line = bisect.bisect_right(line_starts, start) - 1
        base = line_starts[line]
        # Clamp to the end of the starting line; a span never straddles a newline
        # in practice, but a stray one must not bleed into the next rung.
        line_end = line_starts[line + 1] - 1 if line + 1 < len(line_starts) else len(text)
        out.append((line + 1, start - base, min(end, line_end) - base, category))
    return out


def _strip_trailing_empty_rung(rungs: list[list], comments: list[str | None]) -> None:
    """Studio appends the routine's END rung on a whole-routine copy. Drop at most
    one trailing empty rung; never touch a non-trailing empty rung, and never drop
    an empty rung that carries a comment."""
    if rungs and not rungs[-1] and comments[-1] is None:
        rungs.pop()
        comments.pop()


_ORPHAN_COMMENT_WARNING = ("A comment after the last rung belongs to no rung - it is "
                           "shown but not carried into a saved .shs.")


def _comment_list(comments: dict[int, list[str]], count: int) -> list[str | None]:
    """One entry per rung: the rung's comment lines joined by newlines, or None."""
    return ["\n".join(comments[k]) if k in comments else None for k in range(count)]


# ---------------------------------------------------------------------------
# Emitters
# ---------------------------------------------------------------------------
def _neutral_elements(elements: list) -> str:
    out: list[str] = []
    for el in elements:
        if isinstance(el, Instruction):
            out.append(f"{el.mnemonic}({','.join(el.operands)})")
        elif isinstance(el, Branch):
            out.append("[" + ",".join(_neutral_elements(leg) for leg in el.legs) + "]")
        elif isinstance(el, Raw):
            out.append(el.text)
    return "".join(out)


def emit_neutral_lines(rungs: list[list]) -> list[str]:
    """One string per rung, each ending in ';'. Join with '' for the Studio-safe form."""
    return [_neutral_elements(rung) + ";" for rung in rungs]


def emit_neutral(rungs: list[list]) -> str:
    return "".join(emit_neutral_lines(rungs))


def _ascii_tokens(elements: list, warnings: list[str]) -> list[str]:
    out: list[str] = []
    for el in elements:
        if isinstance(el, Instruction):
            out.append(el.mnemonic)
            for op in el.operands:
                if op == "":
                    warnings.append(
                        f"{el.mnemonic} has an empty operand, which cannot be "
                        f"represented in ASCII - it will be lost."
                    )
                    continue
                if any(ch.isspace() for ch in op):
                    # ASCII is space-delimited, so an operand containing spaces
                    # (typically a CPT/CMP/FAL/FSC expression, or a string literal)
                    # must be quoted to stay a single token. This is what Studio does.
                    if '"' in op:
                        warnings.append(
                            f"Operand {op!r} in {el.mnemonic} contains both a space "
                            f"and a double quote; it cannot be quoted unambiguously "
                            f"in ASCII - verify this rung by hand."
                        )
                    out.append('"' + op + '"')
                else:
                    out.append(op)
        elif isinstance(el, Branch):
            out.append(TOK_BST)
            for idx, leg in enumerate(el.legs):
                if idx:
                    out.append(TOK_NXB)
                out.extend(_ascii_tokens(leg, warnings))
            out.append(TOK_BND)
        elif isinstance(el, Raw):
            out.extend(el.text.split())
    return out


def emit_ascii_lines(rungs: list[list]) -> tuple[list[str], list[str]]:
    """One string per rung. Rung 0 carries no leading SOR; every rung ends with EOR."""
    warnings: list[str] = []
    lines: list[str] = []
    for idx, rung in enumerate(rungs):
        toks: list[str] = [] if idx == 0 else [TOK_SOR]
        toks.extend(_ascii_tokens(rung, warnings))
        toks.append(TOK_EOR)
        lines.append(" ".join(toks))
    return lines, list(dict.fromkeys(warnings))


def emit_ascii(rungs: list[list]) -> str:
    lines, _ = emit_ascii_lines(rungs)
    return " ".join(lines)


# ---------------------------------------------------------------------------
# Detection and the conversion pipeline
# ---------------------------------------------------------------------------
def detect(text: str) -> str | None:
    """Return 'Neutral', 'ASCII', 'Unrecognized', or None when the input is empty.

    Comment lines are ignored, so a comment mentioning SOR or BND (or holding a
    ';') never decides the format.
    """
    text = COMMENT_LINE.sub("", text)
    if not text.strip():
        return None

    # Blank out operand lists first, so a tag that happens to be spelled SOR or BND
    # inside XIC(...) does not masquerade as an ASCII keyword.
    stripped = text
    for _ in range(4):
        new = re.sub(r"\([^()]*\)", "()", stripped)
        if new == stripped:
            break
        stripped = new

    if ASCII_KEYWORD.search(stripped):
        return "ASCII"
    if ";" in text and NEUTRAL_CALL.search(text):
        return "Neutral"
    return "Unrecognized"


@dataclass
class Conversion:
    kind: str | None                  # 'Neutral' | 'ASCII' | 'Unrecognized' | None
    output: str = ""                  # single-line, newline-free, Studio-safe
    # Display: one rung per line, each rung's '//' comment lines above it.
    output_lines: list[str] = field(default_factory=list)
    input_lines: list[str] = field(default_factory=list)    # normalized input display
    input_single: str = ""            # the input format, single line, no comments
    neutral_lines: list[str] = field(default_factory=list)  # one per rung, no comments
    comments: list[str | None] = field(default_factory=list)  # one per rung
    warnings: list[str] = field(default_factory=list)
    rung_count: int = 0

    @property
    def output_kind(self) -> str:
        return {"Neutral": "ASCII", "ASCII": "Neutral"}.get(self.kind or "", "")


def convert(text: str) -> Conversion:
    kind = detect(text)
    if kind is None or kind == "Unrecognized":
        return Conversion(kind=kind)

    if kind == "Neutral":
        rungs, warnings, _, comments = _parse_neutral(text)
        out_lines, w2 = emit_ascii_lines(rungs)
        output = " ".join(out_lines)
        in_lines = neutral_lines = emit_neutral_lines(rungs)
        input_single = "".join(in_lines)
    else:
        rungs, warnings, _, comments = _parse_ascii(text)
        out_lines = neutral_lines = emit_neutral_lines(rungs)
        output = "".join(out_lines)
        in_lines, w2 = emit_ascii_lines(rungs)
        input_single = " ".join(in_lines)

    return Conversion(
        kind=kind,
        output=output,
        output_lines=_with_comments(out_lines, comments),
        input_lines=_with_comments(in_lines, comments),
        input_single=input_single,
        neutral_lines=neutral_lines,
        comments=comments,
        warnings=warnings + w2,
        rung_count=len(rungs),
    )


# ---------------------------------------------------------------------------
# Studio rung scrap files (.shs)
# ---------------------------------------------------------------------------
# Dragging rungs out of a Studio routine onto the desktop or a folder makes a file
# named "RSLogix 5000 RLL Scrap '...'.shs"; dragging such a file back into a
# routine inserts its rungs, comments included. Studio's Ctrl+C puts only plain
# text (no comments) on the clipboard, so this file is the only way comments
# travel. Not documented by Rockwell; worked out from Studio's own files and
# confirmed by dragging rebuilt files back into Studio (README, Sources).
#
# Layout:
#   * A fixed 40-byte header: an MFC class tag for "CScrapFileHeader", then the
#     length-prefixed UTF-16 string "rungs".
#   * UTF-16LE text, one entry per LF: "RG: <neutral text>;" is a rung and
#     "RC: <comment>" is the comment of the rung just before it. Line breaks
#     inside a comment are CR LF. There are no counts, lengths or checksums.
#   * Studio writes the whole file in C text mode, so every 0x0A *byte* is stored
#     as 0x0D 0x0A. Reading undoes that and writing redoes it.
SHS_CLASS = b"\xff\xff\x00\x00\x10\x00CScrapFileHeader"
SHS_HEADER = SHS_CLASS + b"\x01\x00\x00\x00\xff\xfe\xff\x05" + "rungs".encode("utf-16-le")
SHS_ENTRY_SPLIT = re.compile(r"(?<!\r)\n")     # an LF that is not part of a CR LF


def is_shs(data: bytes) -> bool:
    return data.startswith(SHS_CLASS)


def read_shs(data: bytes) -> tuple[list[str], list[str | None], list[str]]:
    """Read a Studio rung scrap file into (neutral rung lines, comments, warnings).

    comments[k] is rung k's comment with '\\n' line breaks, or None.
    """
    raw = data.replace(b"\r\n", b"\n")
    if not raw.startswith(SHS_CLASS):
        raise ConversionError("This is not a Studio rung scrap (.shs) file.")
    if not raw.startswith(SHS_HEADER):
        raise ConversionError("This Studio scrap file does not hold ladder rungs, or "
                              "uses a layout this tool has not seen before.")
    try:
        body = raw[len(SHS_HEADER):].decode("utf-16-le")
    except UnicodeDecodeError as exc:
        raise ConversionError(f"The scrap file's text could not be read: {exc}") from exc

    rungs: list[str] = []
    comments: list[str | None] = []
    warnings: list[str] = []
    for entry in SHS_ENTRY_SPLIT.split(body):
        if not entry:
            continue
        tag, _, value = entry.partition(": ")
        if tag == "RG":
            rungs.append(value)
            comments.append(None)
        elif tag == "RC":
            text = value.replace("\r\n", "\n")
            if not rungs:
                warnings.append("The scrap file has a comment before its first rung "
                                "- ignored.")
            elif comments[-1] is None:
                comments[-1] = text
            else:
                comments[-1] += "\n" + text
        else:
            warnings.append(f"Unrecognized entry in the scrap file ignored: {entry[:40]!r}")
    if not rungs:
        raise ConversionError("The scrap file contains no rungs.")
    return rungs, comments, warnings


def shs_pane_text(data: bytes) -> tuple[str, list[str]]:
    """A scrap file as pane text: each rung on its own line, its comment above it."""
    rungs, comments, warnings = read_shs(data)
    return "\n".join(_with_comments(rungs, comments)), warnings


def build_shs(neutral_lines: list[str], comments: list[str | None]) -> bytes:
    """Write rungs and their comments as a Studio rung scrap file."""
    parts: list[str] = []
    for number, (line, comment) in enumerate(zip(neutral_lines, comments)):
        if "\n" in line or "\r" in line:
            raise ConversionError(f"Rung {number} has a line break inside an operand, "
                                  f"which a scrap file cannot hold. Remove it and retry.")
        parts.append(f"RG: {line}\n")
        if comment is not None:
            parts.append("RC: " + comment.replace("\n", "\r\n") + "\n")
    return (SHS_HEADER + "".join(parts).encode("utf-16-le")).replace(b"\n", b"\r\n")


def neutral_to_ascii(text: str) -> str:
    rungs, _ = parse_neutral(text)
    return emit_ascii(rungs)


def ascii_to_neutral(text: str) -> str:
    rungs, _ = parse_ascii(text)
    return emit_neutral(rungs)


# ---------------------------------------------------------------------------
# Self-test fixtures
# ---------------------------------------------------------------------------
# The canonical pair is a real Studio copy of a safety routine, in both formats,
# with the tag names changed. Structure, spacing and instruction order are as
# Studio produced them.
CANON_NEUTRAL = (
    "NOP();"
    "[XIC(SafetyZone[2].ZoneSafe)XIC(SafetyZone[2].QuickStopOK)"
    "XIC(SafetyZone[2].SafeStopOK),XIC(SafetyZone[2].GatesClosed)"
    "XIC(SafetyZone[2].EStopsOK)XIC(SafetyZone[2].FeedbackOK)]"
    "NOP();"
    "[XIC(HMI_SafetyFaultReset_PB),XIC(Cell_DataFromLinePLC.ResetPB)]"
    "OTE(SafetyZone[2].ResetCmd);"
    "XIO(SafetyZone[2].ResetLock)XIC(SafetyZone[2].ResetCmd)"
    "XIC(SafetyZone[2].FeedbackOK)OSF(SafetyZone[2].Edge.1,"
    "SafetyZone[2].ResetFallingEdge);"
    "[XIO(SafetyZone[2].QuickStopOK)ONS(SafetyZone[2].Edge.0)"
    "OTL(SafetyZone[2].ResetLock),XIO(SafetyZone[2].SafeStopOK)"
    "OTU(SafetyZone[2].ResetLock)];"
    "XIC(EStopPB[1].DCS.O1)XIC(EStopPB[2].DCS.O1)XIC(EStopPB[3].DCS.O1)"
    "XIC(EStopPB[4].DCS.O1)XIC(EStopPB[5].DCS.O1)XIC(EStopPB[6].DCS.O1)"
    "OTE(SafetyZone[2].EStopsOK);"
    "OTE(SafetyZone[2].GatesClosed);"
    "OTE(SafetyZone[2].PullCordsOK);"
    "OTE(SafetyZone[2].DrivesSafe);"
    "XIC(Cell_DataFromLinePLC.SafetyPermissive)XIC(SafetyZone[2].EStopsOK)"
    "XIC(SafetyZone[2].GatesClosed)XIC(SafetyZone[2].PullCordsOK)"
    "XIC(SafetyZone[2].DrivesSafe)OTE(SafetyZone[2].ZoneSafe);"
)

CANON_ASCII = (
    "NOP EOR "
    "SOR BST XIC SafetyZone[2].ZoneSafe XIC SafetyZone[2].QuickStopOK "
    "XIC SafetyZone[2].SafeStopOK NXB XIC SafetyZone[2].GatesClosed "
    "XIC SafetyZone[2].EStopsOK XIC SafetyZone[2].FeedbackOK BND "
    "NOP EOR "
    "SOR BST XIC HMI_SafetyFaultReset_PB NXB XIC Cell_DataFromLinePLC.ResetPB BND "
    "OTE SafetyZone[2].ResetCmd EOR "
    "SOR XIO SafetyZone[2].ResetLock XIC SafetyZone[2].ResetCmd "
    "XIC SafetyZone[2].FeedbackOK OSF SafetyZone[2].Edge.1 "
    "SafetyZone[2].ResetFallingEdge EOR "
    "SOR BST XIO SafetyZone[2].QuickStopOK ONS SafetyZone[2].Edge.0 "
    "OTL SafetyZone[2].ResetLock NXB XIO SafetyZone[2].SafeStopOK "
    "OTU SafetyZone[2].ResetLock BND EOR "
    "SOR XIC EStopPB[1].DCS.O1 XIC EStopPB[2].DCS.O1 XIC EStopPB[3].DCS.O1 "
    "XIC EStopPB[4].DCS.O1 XIC EStopPB[5].DCS.O1 XIC EStopPB[6].DCS.O1 "
    "OTE SafetyZone[2].EStopsOK EOR "
    "SOR OTE SafetyZone[2].GatesClosed EOR "
    "SOR OTE SafetyZone[2].PullCordsOK EOR "
    "SOR OTE SafetyZone[2].DrivesSafe EOR "
    "SOR XIC Cell_DataFromLinePLC.SafetyPermissive XIC SafetyZone[2].EStopsOK "
    "XIC SafetyZone[2].GatesClosed XIC SafetyZone[2].PullCordsOK "
    "XIC SafetyZone[2].DrivesSafe OTE SafetyZone[2].ZoneSafe EOR"
)

# Nested branches -- the sample above has none, so this pair is our own fixture.
NESTED_NEUTRAL = (
    "[XIC(Start_PB)[XIC(Mode.Auto),XIC(Mode.Manual)XIC(Enable_SW)]XIC(Guard_Closed),"
    "XIC(Bypass_KeySw)]OTE(Motor_Run);"
    "[[XIC(a),XIC(b)],XIC(c)]OTE(d);"
)
NESTED_ASCII = (
    "BST XIC Start_PB BST XIC Mode.Auto NXB XIC Mode.Manual XIC Enable_SW BND "
    "XIC Guard_Closed NXB XIC Bypass_KeySw BND OTE Motor_Run EOR "
    "SOR BST BST XIC a NXB XIC b BND NXB XIC c BND OTE d EOR"
)

# Expression operands containing spaces. Adapted from a real drive routine with the
# tag names changed; the expected ASCII form (quoted expression) was confirmed
# against Studio.
EXPR_NEUTRAL = (
    "CPT(Drive1.Status.Current,_Drive1:I.OutputCurrent / "
    "(Drive1.Config.MotorFLA *100));"
    "CPT(Drive1.Status.Torque,_Drive1:I.TorqueCurrent / "
    "(Drive1.Config.MotorFLA*100));"
)
EXPR_ASCII = (
    'CPT Drive1.Status.Current "_Drive1:I.OutputCurrent / '
    '(Drive1.Config.MotorFLA *100)" EOR '
    'SOR CPT Drive1.Status.Torque "_Drive1:I.TorqueCurrent / '
    '(Drive1.Config.MotorFLA*100)" EOR'
)

# The other three expression-taking instructions behave identically.
EXPR2_NEUTRAL = (
    "CMP(Speed_A > Speed_B)OTE(Fast);"
    "FAL(Ctl,10,0,ALL,Dst[0],Src[0] + 5);"
    "FSC(Ctl2,10,0,INC,Src[0] >= Limit);"
)
EXPR2_ASCII = (
    'CMP "Speed_A > Speed_B" OTE Fast EOR '
    'SOR FAL Ctl 10 0 ALL Dst[0] "Src[0] + 5" EOR '
    'SOR FSC Ctl2 10 0 INC "Src[0] >= Limit" EOR'
)

# A rung scrap (.shs) laid out exactly as Studio writes one -- invented tags and
# comments, but the structure of a real file: rung 0 uncommented, a NOP() header
# rung with a 4-line banner, and a 1-line comment ending in a line break, which
# Studio keeps. Entries end in LF, comment lines in CR LF, and the final
# .replace() is Studio's text-mode write turning each 0x0A byte into 0x0D 0x0A.
SHS_FIXTURE_TEXT = (
    "RG: [XIC(Gate[1].Unlocked),XIC(Gate[1].Fault)]OTE(Gate[1].Lamp);\n"
    "RG: NOP();\n"
    "RC: ********\r\nAREA 1 SAFETY GATE\r\nGATE 1\r\n********\n"
    "RG: XIC(Gate[1].ResetPB)ONS(Gate[1].Edge.0)OTE(Gate[1].ResetCmd);\n"
    "RC: RESET ON THE FALLING EDGE.\r\n\n"
)
SHS_FIXTURE = (b"\xff\xff\x00\x00\x10\x00CScrapFileHeader\x01\x00\x00\x00\xff\xfe\xff\x05"
               + "rungs".encode("utf-16-le")
               + SHS_FIXTURE_TEXT.encode("utf-16-le")).replace(b"\n", b"\r\n")
SHS_FIXTURE_COMMENTS = [None, "********\nAREA 1 SAFETY GATE\nGATE 1\n********",
                        "RESET ON THE FALLING EDGE.\n"]

# A mid-routine empty rung must survive; exactly one trailing empty rung is dropped.
EMPTY_NEUTRAL = "XIC(a)OTE(b);;XIC(c)OTE(d);"
EMPTY_ASCII = "XIC a OTE b EOR SOR EOR SOR XIC c OTE d EOR"


def _noisy_ascii() -> str:
    """The canonical ASCII with real hand/Studio noise applied."""
    noisy = CANON_ASCII
    # The Estop-string rung loses its EOR and runs straight into the next SOR.
    noisy = noisy.replace(
        "OTE SafetyZone[2].EStopsOK EOR SOR OTE SafetyZone[2].GatesClosed",
        "OTE SafetyZone[2].EStopsOK SOR OTE SafetyZone[2].GatesClosed",
    )
    # Stray double spaces before some EORs.
    noisy = noisy.replace("].ResetCmd EOR", "].ResetCmd  EOR")
    noisy = noisy.replace("].PullCordsOK EOR", "].PullCordsOK   EOR")
    return noisy


def _diff(label: str, got: str, want: str) -> str:
    for idx, (a, b) in enumerate(zip(got, want)):
        if a != b:
            lo = max(0, idx - 45)
            return (
                f"{label}: first difference at char {idx}\n"
                f"  got : ...{got[lo:idx + 45]}\n"
                f"  want: ...{want[lo:idx + 45]}"
            )
    if len(got) != len(want):
        if len(got) > len(want):
            return f"{label}: output is longer by {len(got) - len(want)} chars\n  extra: {got[len(want):][:90]!r}"
        return f"{label}: output is shorter by {len(want) - len(got)} chars\n  missing: {want[len(got):][:90]!r}"
    return f"{label}: mismatch"


def selftest(verbose: bool = True) -> tuple[int, int, list[str]]:
    failures: list[str] = []
    checks: list[tuple[str, str, str]] = []   # (label, got, want)

    # 1. Direct conversion in each direction equals the paired canonical form.
    checks.append(("Neutral -> ASCII (canonical)", neutral_to_ascii(CANON_NEUTRAL), CANON_ASCII))
    checks.append(("ASCII -> Neutral (canonical)", ascii_to_neutral(CANON_ASCII), CANON_NEUTRAL))

    # 2. Lossless round-trips.
    checks.append((
        "Round-trip Neutral -> ASCII -> Neutral",
        ascii_to_neutral(neutral_to_ascii(CANON_NEUTRAL)), CANON_NEUTRAL,
    ))
    checks.append((
        "Round-trip ASCII -> Neutral -> ASCII",
        neutral_to_ascii(ascii_to_neutral(CANON_ASCII)), CANON_ASCII,
    ))

    # 3. Nested branches, both directions and both round-trips.
    checks.append(("Nested Neutral -> ASCII", neutral_to_ascii(NESTED_NEUTRAL), NESTED_ASCII))
    checks.append(("Nested ASCII -> Neutral", ascii_to_neutral(NESTED_ASCII), NESTED_NEUTRAL))
    checks.append((
        "Nested round-trip Neutral -> ASCII -> Neutral",
        ascii_to_neutral(neutral_to_ascii(NESTED_NEUTRAL)), NESTED_NEUTRAL,
    ))

    # 4. Noisy input normalizes to the canonical single-line output.
    checks.append(("Noisy ASCII normalizes", neutral_to_ascii(ascii_to_neutral(_noisy_ascii())), CANON_ASCII))
    checks.append(("Noisy ASCII -> Neutral", ascii_to_neutral(_noisy_ascii()), CANON_NEUTRAL))

    # 5. Newlines between rungs are insignificant on input and never emitted.
    nl_ascii = CANON_ASCII.replace(" SOR ", "\nSOR ")
    checks.append(("Newline-separated ASCII -> Neutral", ascii_to_neutral(nl_ascii), CANON_NEUTRAL))
    nl_neutral = CANON_NEUTRAL.replace(";", ";\n")
    checks.append(("Newline-separated Neutral -> ASCII", neutral_to_ascii(nl_neutral), CANON_ASCII))

    # 6. Raw Studio copy: spaces between rungs plus a trailing empty END rung.
    raw_studio = CANON_NEUTRAL.replace(";", ";   ") + ";"
    checks.append(("Raw Studio neutral (spaces + END rung)", neutral_to_ascii(raw_studio), CANON_ASCII))

    # 7. Empty rungs: mid-routine preserved, one trailing dropped.
    checks.append(("Empty rung Neutral -> ASCII", neutral_to_ascii(EMPTY_NEUTRAL), EMPTY_ASCII))
    checks.append(("Empty rung ASCII -> Neutral", ascii_to_neutral(EMPTY_ASCII), EMPTY_NEUTRAL))
    checks.append((
        "Only one trailing empty rung is stripped",
        neutral_to_ascii("XIC(a);;;"), "XIC a EOR SOR EOR",
    ))

    # 8. Bracket/paren/comma disambiguation inside operands.
    tricky = "MOV(Array[1,2],Dest[3].Sub)EQU(Tag[Idx[0]],7)CPT(Dest,(A+B)*C);"
    checks.append(("Operand brackets/commas survive Neutral -> ASCII",
                   neutral_to_ascii(tricky),
                   "MOV Array[1,2] Dest[3].Sub EQU Tag[Idx[0]] 7 CPT Dest (A+B)*C EOR"))
    checks.append(("Operand brackets/commas round-trip", ascii_to_neutral(neutral_to_ascii(tricky)), tricky))

    # 9. Expression operands with spaces are quoted in ASCII, both directions.
    checks.append(("CPT expression Neutral -> ASCII", neutral_to_ascii(EXPR_NEUTRAL), EXPR_ASCII))
    checks.append(("CPT expression ASCII -> Neutral", ascii_to_neutral(EXPR_ASCII), EXPR_NEUTRAL))
    checks.append(("CPT expression round-trip",
                   ascii_to_neutral(neutral_to_ascii(EXPR_NEUTRAL)), EXPR_NEUTRAL))
    checks.append(("CMP/FAL/FSC expressions Neutral -> ASCII",
                   neutral_to_ascii(EXPR2_NEUTRAL), EXPR2_ASCII))
    checks.append(("CMP/FAL/FSC expressions ASCII -> Neutral",
                   ascii_to_neutral(EXPR2_ASCII), EXPR2_NEUTRAL))

    # An expression that arrives UNQUOTED (hand-written, or from an older build)
    # is re-assembled rather than shattered into separate operands.
    checks.append((
        "Unquoted CPT expression is recovered",
        ascii_to_neutral(EXPR_ASCII.replace('"', "")), EXPR_NEUTRAL,
    ))
    checks.append((
        "Unquoted CMP/FAL/FSC expressions are recovered",
        ascii_to_neutral(EXPR2_ASCII.replace('"', "")), EXPR2_NEUTRAL,
    ))

    # A quoted operand is never mistaken for a mnemonic.
    checks.append((
        "Quoted operand is not classified as a mnemonic",
        ascii_to_neutral('CPT Dest "MOV + XIC"'), 'CPT(Dest,MOV + XIC);',
    ))

    # A string literal containing spaces now survives the trip through ASCII.
    checks.append((
        "String literal with spaces round-trips",
        ascii_to_neutral(neutral_to_ascii("MOV('hello world',Dest);")),
        "MOV('hello world',Dest);",
    ))

    # 9b. Zero-operand instructions.
    checks.append(("Zero-operand instructions", neutral_to_ascii("NOP()AFI()TND();"), "NOP AFI TND EOR"))

    # 10. RM014's own branch form, which uses a leading comma (empty first leg).
    #     1756-RM014D-EN-P p.210: N: XIC(conveyor_a)[,XIC(input_1) XIO(input_2) ]OTE(light_1);
    rm014 = "XIC(conveyor_a)[,XIC(input_1) XIO(input_2) ]OTE(light_1);"
    checks.append((
        "RM014 leading-comma branch (empty first leg)",
        neutral_to_ascii(rm014),
        "XIC conveyor_a BST NXB XIC input_1 XIO input_2 BND OTE light_1 EOR",
    ))
    checks.append((
        "RM014 leading-comma branch round-trips",
        ascii_to_neutral(neutral_to_ascii(rm014)),
        "XIC(conveyor_a)[,XIC(input_1)XIO(input_2)]OTE(light_1);",
    ))

    # 11. Emitted output must never contain a newline.
    for label, produced in (
        ("ASCII output", neutral_to_ascii(CANON_NEUTRAL)),
        ("Neutral output", ascii_to_neutral(CANON_ASCII)),
    ):
        if "\n" in produced or "\r" in produced:
            failures.append(f"{label} contains a newline character")

    # 11b. Syntax highlighting spans land on exactly the right substrings.
    def _tagged(sample: str, kind: str) -> list[tuple[str, str]]:
        lines = sample.split("\n")
        return [(lines[ln - 1][a:b], cat)
                for ln, a, b, cat in highlight_spans(sample, kind)]

    hl_ascii = ('SOR BST XIC Tank_Enable NXB Example_Aoi_1 Tag_A BND '
                'TON T1 5000 0 CPT D "A / (B *100)" EOR')
    checks.append((
        "Highlight spans, ASCII",
        repr(_tagged(hl_ascii, "ASCII")),
        repr([("SOR", "rung"), ("BST", "branch"), ("XIC", "instruction"),
              ("NXB", "branch"), ("Example_Aoi_1", "aoi"), ("BND", "branch"),
              ("TON", "instruction"), ("5000", "literal"), ("0", "literal"),
              ("CPT", "instruction"), ('"A / (B *100)"', "expression"),
              ("EOR", "rung")]),
    ))

    hl_neutral = "[XIC(Tank_Enable),Example_Aoi_1(Tag_A)]TON(T1,5000,0)CPT(D,A / (B *100));"
    checks.append((
        "Highlight spans, Neutral",
        repr(_tagged(hl_neutral, "Neutral")),
        repr([("[", "branch"), ("XIC", "instruction"), (",", "branch"),
              ("Example_Aoi_1", "aoi"), ("]", "branch"), ("TON", "instruction"),
              ("5000", "literal"), ("0", "literal"), ("CPT", "instruction"),
              ("A / (B *100)", "expression"), (";", "rung")]),
    ))

    # Spans must stay inside their own display line when rungs are one per line.
    multi = "XIC a OTE b EOR\nSOR XIC c OTE d EOR"
    for ln, a, b, _cat in highlight_spans(multi, "ASCII"):
        if b > len(multi.split("\n")[ln - 1]):
            failures.append(f"highlight span {(ln, a, b)} runs past the end of its line")

    # Radix and string literals count as raw values; tags never do.
    for token, want_literal in (("5000", True), ("16#000F", True), ("2#0000_1111", True),
                                ("-12.5", True), ("'text'", True),
                                ("Tank_Enable", False), ("Zone[2].Bit", False),
                                ("_Drive1:I.Data", False), ("ALL", False)):
        if _is_literal(token) != want_literal:
            failures.append(f"_is_literal({token!r}) should be {want_literal}")

    # 11c. Logix v36 renamed 16 mnemonics; both spellings must be recognized.
    checks.append((
        "v36 mnemonics ASCII -> Neutral",
        ascii_to_neutral(
            "EQ Speed_A Speed_B MOVE Src Dst EOR "
            "SOR LIMIT Lo_Lim Test Hi_Lim GT A_Val B_Val EOR "
            "SOR TRUNC In_Val Out_Val TO_BCD Raw_Val Bcd_Val EOR "
            "SOR ACOS X_Val Y_Val SQRT S_Val R_Val EXPT P_Val Q_Val EOR"
        ),
        "EQ(Speed_A,Speed_B)MOVE(Src,Dst);"
        "LIMIT(Lo_Lim,Test,Hi_Lim)GT(A_Val,B_Val);"
        "TRUNC(In_Val,Out_Val)TO_BCD(Raw_Val,Bcd_Val);"
        "ACOS(X_Val,Y_Val)SQRT(S_Val,R_Val)EXPT(P_Val,Q_Val);",
    ))
    checks.append((
        "Legacy mnemonics still work alongside them",
        ascii_to_neutral("EQU A B MOV S D EOR SOR LIM L T H GRT X Y EOR"),
        "EQU(A,B)MOV(S,D);LIM(L,T,H)GRT(X,Y);",
    ))

    for legacy, current in V36_MNEMONIC_UPDATES.items():
        for mnemonic in (legacy, current):
            if mnemonic not in INSTRUCTION_SET:
                failures.append(f"{mnemonic} missing from the known instruction set")
            if not classify_token(mnemonic):
                failures.append(f"{mnemonic} is not classified as a mnemonic")

    # These four are the dangerous ones: too long, or containing '_', so the shape
    # heuristic alone reads them as operands and silently folds them into the
    # preceding instruction.
    for mnemonic in ("LIMIT", "TRUNC", "TO_BCD", "BCD_TO"):
        if MNEMONIC_SHAPE.match(mnemonic):
            failures.append(f"{mnemonic} unexpectedly matches the shape heuristic")
        got = ascii_to_neutral(f"XIC Enable_Bit {mnemonic} A_Val B_Val EOR")
        want = f"XIC(Enable_Bit){mnemonic}(A_Val,B_Val);"
        if got != want:
            failures.append(f"{mnemonic} was folded into the previous instruction: {got}")

    # None of the v36 spellings may raise the unrecognized-instruction warning.
    _, v36_warnings = parse_ascii(
        "EQ A B GT C D GE E F LE G H LT I J NE K L MOVE M N LIMIT O P Q "
        "TRUNC R S SQRT T U EXPT V W ACOS X Y ASIN Z A1 ATAN B1 C1 "
        "TO_BCD D1 E1 BCD_TO F1 G1 EOR"
    )
    if any("Unrecognized instruction" in w for w in v36_warnings):
        failures.append(f"v36 mnemonics were flagged as unrecognized: {v36_warnings}")

    # 11d. Branch nesting is capped, and refused cleanly rather than crashing.
    deep_neutral = "[" * (MAX_BRANCH_DEPTH + 5) + "XIC(a)" + "]" * (MAX_BRANCH_DEPTH + 5) + ";"
    deep_ascii = "BST " * (MAX_BRANCH_DEPTH + 5) + "XIC a " + "BND " * (MAX_BRANCH_DEPTH + 5) + "EOR"
    for label, sample, kind in (("Neutral", deep_neutral, "Neutral"),
                                ("ASCII", deep_ascii, "ASCII")):
        try:
            convert(sample)
            failures.append(f"over-deep {label} nesting was accepted instead of refused")
        except ConversionError:
            pass
        except RecursionError:
            failures.append(f"over-deep {label} nesting still raises RecursionError")
        # highlight_spans walks the same parsers, so it must refuse identically
        try:
            highlight_spans(sample, kind)
            failures.append(f"highlight_spans accepted over-deep {label} nesting")
        except ConversionError:
            pass
        except RecursionError:
            failures.append(f"highlight_spans still raises RecursionError on {label}")

    # A pile of unclosed brackets is the realistic trigger: a mis-picked file.
    try:
        convert("[" * 5000 + "XIC(a);")
        failures.append("5000 unclosed '[' was accepted instead of refused")
    except ConversionError:
        pass
    except RecursionError:
        failures.append("5000 unclosed '[' still raises RecursionError")

    # Just under the cap must still convert normally, both directions.
    ok_depth = MAX_BRANCH_DEPTH - 1
    ok_neutral = "[" * ok_depth + "XIC(a)" + "]" * ok_depth + ";"
    try:
        round_tripped = ascii_to_neutral(neutral_to_ascii(ok_neutral))
        if round_tripped != ok_neutral:
            failures.append(f"nesting at depth {ok_depth} did not round-trip")
    except Exception as exc:                                   # noqa: BLE001
        failures.append(f"nesting at depth {ok_depth} should convert, raised {exc!r}")

    # 12. Format detection.
    for text, want in (
        (CANON_NEUTRAL, "Neutral"),
        (CANON_ASCII, "ASCII"),
        ("", None),
        ("   ", None),
        ("hello world", "Unrecognized"),
        ("XIC(SOR)OTE(BND);", "Neutral"),      # keyword-shaped tags inside operands
    ):
        got = detect(text)
        if got != want:
            failures.append(f"detect({text[:32]!r}) returned {got!r}, expected {want!r}")

    # 12b. Rung comments and Studio rung scrap (.shs) files.
    if b"\x0d\x00\x0d\x0a\x00" not in SHS_FIXTURE:
        failures.append("SHS fixture lost Studio's text-mode CR LF expansion")
    shs_rungs, shs_comments, shs_warn = read_shs(SHS_FIXTURE)
    checks.append(("Scrap file: rungs read",
                   repr(shs_rungs),
                   repr(["[XIC(Gate[1].Unlocked),XIC(Gate[1].Fault)]OTE(Gate[1].Lamp);",
                         "NOP();",
                         "XIC(Gate[1].ResetPB)ONS(Gate[1].Edge.0)OTE(Gate[1].ResetCmd);"])))
    checks.append(("Scrap file: each comment goes to the rung before it",
                   repr(shs_comments), repr(SHS_FIXTURE_COMMENTS)))
    checks.append(("Scrap file rebuilds byte for byte",
                   build_shs(shs_rungs, shs_comments).hex(), SHS_FIXTURE.hex()))
    pane_text, _ = shs_pane_text(SHS_FIXTURE)
    checks.append(("Scrap file shows comments above their rung",
                   pane_text,
                   "[XIC(Gate[1].Unlocked),XIC(Gate[1].Fault)]OTE(Gate[1].Lamp);\n"
                   "// ********\n// AREA 1 SAFETY GATE\n// GATE 1\n// ********\n"
                   "NOP();\n"
                   "// RESET ON THE FALLING EDGE.\n//\n"
                   "XIC(Gate[1].ResetPB)ONS(Gate[1].Edge.0)OTE(Gate[1].ResetCmd);"))
    via_pane = convert(pane_text)
    checks.append(("Scrap -> pane -> scrap is byte for byte",
                   build_shs(via_pane.neutral_lines, via_pane.comments).hex(),
                   SHS_FIXTURE.hex()))
    via_ascii = convert("\n".join(via_pane.output_lines))
    checks.append(("Comments survive the ASCII side and back",
                   build_shs(via_ascii.neutral_lines, via_ascii.comments).hex(),
                   SHS_FIXTURE.hex()))
    for label, single in (("ASCII output", via_pane.output),
                          ("single-line input", via_pane.input_single)):
        if "//" in single or "AREA" in single or "\n" in single:
            failures.append(f"comment text leaked into the {label}: {single[:60]!r}")
    checks.append((
        "A comment belongs to the next rung, even after a missing EOR",
        repr(convert("XIC a OTE b\n// note\nSOR XIC c OTE d EOR").comments),
        repr([None, "note"]),
    ))
    checks.append((
        "'//' after logic on the same line is not a comment",
        repr([c for c in convert("XIC(a)OTE(b);\n  // indented is fine\n"
                                 "XIC(c)OTE(d); // not this").comments if c]),
        repr(["indented is fine"]),
    ))
    checks.append((
        "A commented empty last rung is kept",
        repr(convert("XIC(a);\n// spare\n;").comments), repr([None, "spare"]),
    ))
    if detect("// SOR BND ; EOR\nXIC(a)OTE(b);") != "Neutral":
        failures.append("a comment mentioning ASCII keywords changed the detected format")
    if detect("// only a comment") is not None:
        failures.append("comment-only input should count as empty")
    if not any("belongs to no rung" in w for w in convert("XIC(a);\n// orphan").warnings):
        failures.append("a comment after the last rung raised no warning")
    if ("comment" not in [cat for *_, cat in highlight_spans("// hi\nXIC(a);", "Neutral")]
            or "comment" not in [cat for *_, cat in highlight_spans("// hi\nXIC a EOR", "ASCII")]):
        failures.append("comment lines are not highlighted as comments")
    for label, bad in (("not a scrap", b"hello"),
                       ("not rungs", SHS_CLASS + b"\x01\x00\x00\x00\xff\xfe\xff\x04"
                                     + "tags".encode("utf-16-le"))):
        try:
            read_shs(bad)
            failures.append(f"read_shs accepted a file that is {label}")
        except ConversionError:
            pass
    try:
        build_shs(["CPT(d,a +\nb);"], [None])
        failures.append("build_shs wrote a rung containing a line break")
    except ConversionError:
        pass

    # 13. Unrecognized mnemonics warn but are never dropped.
    rungs, warns = parse_ascii("SOR EXAMPLE_AOI Tag_A XYZQ Tag_B EOR")
    emitted = emit_neutral(rungs)
    if "EXAMPLE_AOI" not in emitted or "XYZQ" not in emitted or "Tag_B" not in emitted:
        failures.append(f"AOI tokens were dropped: {emitted}")
    if not any("Unrecognized instruction" in w for w in warns):
        failures.append(f"expected an unrecognized-instruction warning, got {warns}")

    for label, got, want in checks:
        if got != want:
            failures.append(_diff(label, got, want))

    total = len(checks) + 3
    passed = total - len(failures)

    if verbose:
        print(f"{APP_TITLE}\nSelf-test\n" + "-" * 60)
        for label, got, want in checks:
            print(f"  [{'PASS' if got == want else 'FAIL'}] {label}")
        print("-" * 60)
        if failures:
            print(f"{len(failures)} FAILURE(S):\n")
            for f in failures:
                print(f + "\n")
        else:
            print(f"All checks passed ({len(checks)} conversions + detection + "
                  f"newline-free + AOI passthrough).")
        print(f"Known instruction set: {len(INSTRUCTION_SET)} mnemonics "
              f"= {len(KNOWN_INSTRUCTIONS)} from 1756-RM014D-EN-P pp. 210-221 "
              f"+ {len(V36_MNEMONIC_UPDATES)} renamed in Logix v36 "
              f"+ {len(AOI_INSTRUCTIONS)} user AOI(s).")

    return passed, total, failures


# ---------------------------------------------------------------------------
# Saved settings
# ---------------------------------------------------------------------------
HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")


def settings_path() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "Studio5000RungConverter", "settings.json")


def load_syntax_colors() -> dict[str, str]:
    """Read the saved palette, falling back to the defaults for anything missing
    or malformed. Never raises -- a bad settings file just means defaults."""
    colors = dict(DEFAULT_SYNTAX_COLORS)
    try:
        with open(settings_path(), encoding="utf-8") as fh:
            saved = json.load(fh)
    except (OSError, ValueError):
        return colors
    for key, value in (saved.get("syntax_colors") or {}).items():
        if key in colors and isinstance(value, str) and HEX_COLOR.match(value):
            colors[key] = value
    return colors


def save_syntax_colors(colors: dict[str, str]) -> bool:
    path = settings_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"syntax_colors": colors}, fh, indent=2)
    except OSError:
        return False
    return True


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------
def launch_gui() -> None:
    import tkinter as tk
    from tkinter import colorchooser, filedialog, messagebox, ttk

    MONO = ("Consolas", 10)
    GUTTER_BG = "#f0f0f0"
    GUTTER_FG = "#808080"
    WARN_BG = "#fff4c2"
    WARN_FG = "#6b5300"
    ERR_BG = "#ffd9d9"
    ERR_FG = "#8a1f1f"
    FIND_ALL_BG = "#c9c9c9"

    class ProxyText(tk.Text):
        """A Text widget that emits <<Changed>> for edits and scrolls, including
        programmatic ones, so the gutter can stay in sync."""

        def __init__(self, master, **kw):
            super().__init__(master, **kw)
            self.edit_serial = 0         # bumped on every content change
            self._orig = str(self) + "_orig"
            self.tk.call("rename", str(self), self._orig)
            self.tk.createcommand(str(self), self._proxy)

        def _proxy(self, *args):
            try:
                result = self.tk.call((self._orig,) + args)
            except tk.TclError:
                return ""
            if args and args[0] in ("insert", "delete", "replace"):
                self.edit_serial += 1
            if args and args[0] in ("insert", "delete", "replace", "yview", "xview", "see"):
                try:
                    self.event_generate("<<Changed>>", when="tail")
                except tk.TclError:
                    pass
            return result

    class RungPane(ttk.Frame):
        """A monospaced text pane with a rung-number gutter on its left.

        The gutter numbers rungs starting at 0, matching Studio. This works because
        the pane displays one rung per line -- display only; the copied/saved text
        is always the single-line form.
        """

        def __init__(self, master, editable: bool, on_change=None):
            super().__init__(master)
            self.on_change = on_change
            self._syncing = False
            self.partner: RungPane | None = None
            self.sync_scroll = None       # tk.BooleanVar, injected by the app

            self.gutter = tk.Canvas(self, width=46, bg=GUTTER_BG, highlightthickness=0,
                                    borderwidth=0)
            self.gutter.pack(side="left", fill="y")

            self.vbar = ttk.Scrollbar(self, orient="vertical", command=self._on_vbar)
            self.vbar.pack(side="right", fill="y")

            self.hbar = ttk.Scrollbar(self, orient="horizontal")
            self.text = ProxyText(
                self, wrap="none", font=MONO, undo=True, maxundo=-1,
                autoseparators=True, borderwidth=0, highlightthickness=1,
                yscrollcommand=self._on_yscroll, xscrollcommand=self.hbar.set,
            )
            self.hbar.config(command=self.text.xview)
            self.hbar.pack(side="bottom", fill="x")
            self.text.pack(side="left", fill="both", expand=True)

            self.editable = editable
            if not editable:
                self.text.config(state="disabled", background="#fafafa")

            self.text.bind("<Double-Button-1>", self._on_double_click)
            self.text.bind("<<Changed>>", lambda e: self._schedule_redraw())
            self.text.bind("<Configure>", lambda e: self._schedule_redraw())
            self.text.bind("<KeyRelease>", self._on_edit)
            self.text.bind("<<Paste>>", self._on_paste)
            self.text.bind("<<Cut>>", self._on_edit)
            self.text.bind("<MouseWheel>", lambda e: self._schedule_redraw())
            self._redraw_job = None
            self._pending_paste = False
            self._numbers: list[int | None] = []
            self._numbers_serial = -1

        # -- scrolling ----------------------------------------------------
        def _on_vbar(self, *args):
            self.text.yview(*args)

        def _on_yscroll(self, first, last):
            self.vbar.set(first, last)
            self._schedule_redraw()
            if (self.partner is not None and self.sync_scroll is not None
                    and self.sync_scroll.get() and not self._syncing):
                self.partner._syncing = True
                try:
                    self.partner.text.yview_moveto(float(first))
                finally:
                    self.partner._syncing = False

        # -- editing ------------------------------------------------------
        def _on_edit(self, _event=None):
            self._schedule_redraw()
            if self.on_change:
                self.on_change(paste=False)

        def _on_paste(self, _event=None):
            # Let Tk insert the text first, then reformat: a Studio paste is a
            # single-line blob and the user wants to see it as one rung per line.
            self.after_idle(lambda: self.on_change(paste=True) if self.on_change else None)

        # -- content ------------------------------------------------------
        def get(self) -> str:
            return self.text.get("1.0", "end-1c")

        def set(self, value: str, keep_view: bool = False) -> None:
            first = self.text.yview()[0]
            was_disabled = str(self.text.cget("state")) == "disabled"
            if was_disabled:
                self.text.config(state="normal")
            self.text.delete("1.0", "end")
            self.text.insert("1.0", value)
            if was_disabled:
                self.text.config(state="disabled")
            if keep_view:
                self.text.yview_moveto(first)
            self._schedule_redraw()

        # -- syntax highlighting ------------------------------------------
        def configure_syntax(self, colors: dict) -> None:
            for category in SYNTAX_CATEGORIES:
                self.text.tag_configure(category,
                                        foreground=colors[category])
            # Background only, so it composes with the foreground syntax colours.
            self.text.tag_configure(TAG_FIND_ALL, background=FIND_ALL_BG)
            # Keep the selection readable on top of coloured tokens.
            self.text.tag_raise("sel")

        def highlight_matches(self, ranges) -> None:
            """Grey-background every other occurrence of the current find term.

            The active match keeps the normal blue selection: 'sel' is raised above
            this tag, so the one Replace will act on stays visually distinct.
            """
            self.text.tag_remove(TAG_FIND_ALL, "1.0", "end")
            for line, start, end in ranges:
                if end > start:
                    self.text.tag_add(TAG_FIND_ALL, f"{line}.{start}", f"{line}.{end}")
            self.text.tag_raise("sel")

        def apply_spans(self, spans) -> None:
            for category in SYNTAX_CATEGORIES:
                self.text.tag_remove(category, "1.0", "end")
            for line, start, end, category in spans:
                if end > start:
                    self.text.tag_add(category, f"{line}.{start}", f"{line}.{end}")
            self.text.tag_raise("sel")

        # -- double-click selection ---------------------------------------
        def select_word_at(self, line: int, col: int) -> tuple[int, int] | None:
            """Range of the token at (line, col), bounded by WORD_DELIMITERS.

            Returns None when the click lands on whitespace. Clicking directly on
            a non-space delimiter selects just that character.
            """
            body = self.text.get(f"{line}.0", f"{line}.end")
            if not body:
                return None
            col = min(col, len(body) - 1)
            if body[col] in WORD_DELIMITERS:
                return None if body[col].isspace() else (col, col + 1)
            start = col
            while start > 0 and body[start - 1] not in WORD_DELIMITERS:
                start -= 1
            end = col
            while end < len(body) and body[end] not in WORD_DELIMITERS:
                end += 1
            return (start, end)

        def _on_double_click(self, event):
            index = self.text.index(f"@{event.x},{event.y}")
            line, col = (int(part) for part in str(index).split("."))
            span = self.select_word_at(line, col)
            self.text.tag_remove("sel", "1.0", "end")
            if span:
                start, end = span
                self.text.tag_add("sel", f"{line}.{start}", f"{line}.{end}")
                self.text.mark_set("insert", f"{line}.{end}")
            else:
                self.text.mark_set("insert", index)
            return "break"      # suppress Tk's own whole-rung word selection

        def set_wrap(self, wrap: bool) -> None:
            self.text.config(wrap="word" if wrap else "none")
            if wrap:
                self.hbar.pack_forget()
            else:
                self.hbar.pack(side="bottom", fill="x", before=self.text)
            self._schedule_redraw()

        # -- gutter -------------------------------------------------------
        def _schedule_redraw(self):
            if self._redraw_job is None:
                self._redraw_job = self.after_idle(self._redraw_gutter)

        def rung_numbers(self) -> list[int | None]:
            """Per pane line, the rung number to show: comment lines get None, and
            the other lines count up from 0. Recomputed only after an edit."""
            if self._numbers_serial != self.text.edit_serial:
                numbers: list[int | None] = []
                rung = 0
                for line in self.get().split("\n"):
                    if COMMENT_LINE.fullmatch(line):
                        numbers.append(None)
                    else:
                        numbers.append(rung)
                        rung += 1
                self._numbers = numbers
                self._numbers_serial = self.text.edit_serial
            return self._numbers

        def _redraw_gutter(self):
            self._redraw_job = None
            try:
                self.gutter.delete("all")
            except tk.TclError:
                return
            numbers = self.rung_numbers()
            last = int(self.text.index("end-1c").split(".")[0])
            index = self.text.index("@0,0")
            lineno = int(str(index).split(".")[0])
            while lineno <= last:
                info = self.text.dlineinfo(index)
                if info is None:
                    break
                rung = numbers[lineno - 1] if lineno - 1 < len(numbers) else None
                if rung is not None:
                    self.gutter.create_text(
                        40, info[1], anchor="ne", text=str(rung), font=MONO,
                        fill=GUTTER_FG,
                    )
                # Step to the START of the next logical line. "+1line" would keep the
                # column, which under word wrap lands on a continuation row.
                lineno += 1
                index = f"{lineno}.0"

    class App(ttk.Frame):
        def __init__(self, master):
            super().__init__(master, padding=6)
            self.pack(fill="both", expand=True)

            self.current_output = ""            # single-line, Studio-safe
            self.current_input_single = ""       # single-line form of the input
            self.last_error: str | None = None   # set when a conversion raised
            self.current_input_lines: list[str] = []
            self.current_output_lines: list[str] = []   # display form, with comments
            self.current_neutral_lines: list[str] = []  # for a .shs, one per rung
            self.current_comments: list[str | None] = []
            self.current_warnings: list[str] = []
            self._convert_job = None
            self._suppress = False

            self.wrap_var = tk.BooleanVar(value=True)
            self.sync_var = tk.BooleanVar(value=True)
            self.case_var = tk.BooleanVar(value=False)
            self.word_var = tk.BooleanVar(value=False)
            self.comments_var = tk.BooleanVar(value=False)   # Include comments
            self.hilite_var = tk.BooleanVar(value=True)
            self.find_var = tk.StringVar()
            self.repl_var = tk.StringVar()
            self.find_history: list[str] = []
            self.repl_history: list[str] = []
            self.syntax_colors = load_syntax_colors()

            self._build_toolbar()
            self._build_findbar()
            self._build_panes()
            self._build_status()
            self._build_menu(master)

            self.input_pane.configure_syntax(self.syntax_colors)
            self.output_pane.configure_syntax(self.syntax_colors)
            self.apply_wrap()      # wrap_var defaults on; match the widgets to it

            self.input_pane.partner = self.output_pane
            self.output_pane.partner = self.input_pane
            self.input_pane.sync_scroll = self.sync_var
            self.output_pane.sync_scroll = self.sync_var

            master.bind("<Control-f>", lambda e: self.toggle_find(True))
            master.bind("<Control-h>", lambda e: self.toggle_find(True))
            master.bind("<Escape>", lambda e: self.toggle_find(False))
            master.bind("<Control-Alt-t>", lambda e: self.run_selftest())
            self._build_shortcuts(master)
            self.input_pane.text.focus_set()

        def _build_shortcuts(self, master):
            """Office-style Alt accelerators, matching the underlined button letters."""
            for key, command in (
                ("c", self.copy_output),
                ("t", self.copy_input),
                ("s", self.swap),
                ("f", self.format_input),
                ("a", self.replace_all),
                ("r", self.replace_one),
                ("d", self.find_next),
                ("w", self.toggle_wrap),
            ):
                for variant in (key, key.upper()):
                    master.bind_all(
                        f"<Alt-{variant}>",
                        lambda _e, c=command: (c(), "break")[1],
                    )

        # -- construction -------------------------------------------------
        def _build_toolbar(self):
            bar = ttk.Frame(self)
            bar.pack(fill="x", pady=(0, 4))
            ttk.Button(bar, text="Load file…", command=self.load_file).pack(side="left")
            ttk.Button(bar, text="Save output…", command=self.save_file).pack(side="left", padx=(4, 0))
            ttk.Button(bar, text="Save .shs for Studio…",
                       command=self.save_shs).pack(side="left", padx=(4, 0))
            ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=6)
            # underline= marks the Alt accelerator letter, Office-style.
            ttk.Button(bar, text="Copy output (Studio-safe, 1 line)", underline=0,
                       command=self.copy_output).pack(side="left")
            ttk.Button(bar, text="Copy input (1 line)", underline=9,
                       command=self.copy_input).pack(side="left", padx=(4, 0))
            ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=6)
            ttk.Button(bar, text="⇄ Swap", underline=2,
                       command=self.swap).pack(side="left")
            ttk.Button(bar, text="Format rungs", underline=0,
                       command=self.format_input).pack(side="left", padx=(4, 0))
            ttk.Button(bar, text="Clear", command=self.clear).pack(side="left", padx=(4, 0))
            ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=6)
            ttk.Checkbutton(bar, text="Word wrap", underline=0, variable=self.wrap_var,
                            command=self.apply_wrap).pack(side="left")
            ttk.Checkbutton(bar, text="Sync scroll", variable=self.sync_var).pack(side="left", padx=(6, 0))
            ttk.Button(bar, text="Find & Replace",
                       command=lambda: self.toggle_find()).pack(side="right")

        def _build_findbar(self):
            self.findbar = ttk.Frame(self)

            # Excel-style history: Down arrow opens the list (ttk::combobox binds
            # <Down> to Post natively), Up/Down walk it, Return picks.
            ttk.Label(self.findbar, text="Find:").pack(side="left")
            e1 = ttk.Combobox(self.findbar, textvariable=self.find_var, width=24,
                              values=self.find_history)
            e1.pack(side="left", padx=(4, 8))
            e1.bind("<Return>", lambda e: self.find_next())
            self.find_entry = e1
            self.find_combo = e1

            ttk.Label(self.findbar, text="Replace:").pack(side="left")
            e2 = ttk.Combobox(self.findbar, textvariable=self.repl_var, width=24,
                              values=self.repl_history)
            e2.pack(side="left", padx=(4, 8))
            e2.bind("<Return>", lambda e: self.replace_one())
            self.repl_combo = e2

            ttk.Button(self.findbar, text="Find next", underline=3,
                       command=self.find_next).pack(side="left")
            ttk.Button(self.findbar, text="Replace", underline=0,
                       command=self.replace_one).pack(side="left", padx=(4, 0))
            ttk.Button(self.findbar, text="Replace all", underline=8,
                       command=self.replace_all).pack(side="left", padx=(4, 0))
            ttk.Checkbutton(self.findbar, text="Highlight all",
                            variable=self.hilite_var,
                            command=self.refresh_find_highlight).pack(side="left", padx=(8, 0))
            ttk.Checkbutton(self.findbar, text="Case-sensitive",
                            variable=self.case_var, command=self.update_match_count).pack(side="left", padx=(4, 0))
            ttk.Checkbutton(self.findbar, text="Whole word",
                            variable=self.word_var, command=self.update_match_count).pack(side="left", padx=(4, 0))
            ttk.Checkbutton(self.findbar, text="Include comments",
                            variable=self.comments_var, command=self.update_match_count).pack(side="left", padx=(4, 0))
            self.match_label = ttk.Label(self.findbar, text="", foreground="#555")
            self.match_label.pack(side="left", padx=(10, 0))
            ttk.Button(self.findbar, text="✕",
                       command=lambda: self.toggle_find(False), width=3).pack(side="right")
            self.find_var.trace_add("write", lambda *a: self.update_match_count())
            ttk.Label(self.findbar, text="(input pane only)",
                      foreground="#777").pack(side="right", padx=(0, 8))

            # Find & Replace is open by default.
            self.findbar.pack(fill="x", pady=(0, 4))
            self.find_visible = True

        def _build_panes(self):
            panes = ttk.PanedWindow(self, orient="horizontal")
            panes.pack(fill="both", expand=True)

            left = ttk.Frame(panes)
            head_l = ttk.Frame(left)
            head_l.pack(fill="x")
            ttk.Label(head_l, text="Input", font=("Segoe UI", 9, "bold")).pack(side="left")
            self.detect_label = ttk.Label(head_l, text="—", foreground="#0a6", font=("Segoe UI", 9, "bold"))
            self.detect_label.pack(side="right")
            self.input_pane = RungPane(left, editable=True, on_change=self.on_input_change)
            self.input_pane.pack(fill="both", expand=True, pady=(2, 0))
            panes.add(left, weight=1)

            right = ttk.Frame(panes)
            head_r = ttk.Frame(right)
            head_r.pack(fill="x")
            ttk.Label(head_r, text="Output", font=("Segoe UI", 9, "bold")).pack(side="left")
            self.output_label = ttk.Label(head_r, text="", foreground="#555", font=("Segoe UI", 9, "bold"))
            self.output_label.pack(side="right")
            self.output_pane = RungPane(right, editable=False)
            self.output_pane.pack(fill="both", expand=True, pady=(2, 0))
            panes.add(right, weight=1)

        def _build_status(self):
            self.warn_label = tk.Label(self, text="", bg=WARN_BG, fg=WARN_FG, anchor="w",
                                       padx=8, pady=4, justify="left", wraplength=1100)
            self.status = ttk.Label(self, text="Paste rungs copied from Studio 5000 into the "
                                               "input pane.", foreground="#555")
            self.status.pack(fill="x", pady=(4, 0))

        def _build_menu(self, master):
            menubar = tk.Menu(master)
            filemenu = tk.Menu(menubar, tearoff=0)
            filemenu.add_command(label="Load file…", command=self.load_file)
            filemenu.add_command(label="Save output…", command=self.save_file)
            filemenu.add_command(label="Save .shs for Studio…", command=self.save_shs)
            filemenu.add_separator()
            filemenu.add_command(label="Exit", command=master.destroy)
            menubar.add_cascade(label="File", menu=filemenu)

            viewmenu = tk.Menu(menubar, tearoff=0)
            viewmenu.add_command(label="Syntax colours…",
                                 command=self.open_color_settings)
            menubar.add_cascade(label="View", menu=viewmenu)

            helpmenu = tk.Menu(menubar, tearoff=0)
            helpmenu.add_command(label="About", command=self.show_about)
            helpmenu.add_separator()
            helpmenu.add_command(label="Run self-test (dev)\tCtrl+Alt+T",
                                 command=self.run_selftest)
            menubar.add_cascade(label="Help", menu=helpmenu)
            master.config(menu=menubar)

        # -- conversion ---------------------------------------------------
        def on_input_change(self, paste: bool = False):
            if self._suppress:
                return
            if self._convert_job is not None:
                self.after_cancel(self._convert_job)
            delay = 0 if paste else 250
            self._convert_job = self.after(delay, lambda: self.do_convert(reformat=paste))

        def do_convert(self, reformat: bool = False):
            """Convert, and guarantee the panes never keep a stale result.

            Tk swallows exceptions raised inside after() callbacks, so without this
            guard a failed conversion would leave the previous input's output on
            screen -- and in the clipboard -- with no sign anything went wrong.
            """
            self._convert_job = None
            try:
                self._convert_now(reformat)
            except ConversionError as exc:
                self._conversion_failed(str(exc))
            except RecursionError:
                self._conversion_failed(
                    "Input is nested too deeply to convert. This usually means the "
                    "text is not ladder logic."
                )
            except Exception as exc:                       # noqa: BLE001 - backstop
                self._conversion_failed(
                    f"Unexpected {type(exc).__name__} while converting: {exc}"
                )

        def _conversion_failed(self, message: str):
            """Clear everything a stale result could hide in, then say so loudly."""
            self._reset_output()
            self.last_error = message
            self.detect_label.config(text="Conversion failed", foreground="#c00")
            self.set_warning(message, error=True)
            self.status.config(
                text="Conversion failed — output cleared so nothing stale can be copied."
            )

        def _convert_now(self, reformat: bool = False):
            self.last_error = None
            text = self.input_pane.get()
            result = convert(text)

            if result.kind is None:
                self.detect_label.config(text="—", foreground="#888")
                self._reset_output()
                self.set_warning("")
                self.status.config(text="Paste rungs copied from Studio 5000 into the input pane.")
                return

            if result.kind == "Unrecognized":
                self.detect_label.config(text="Unrecognized", foreground="#c00")
                self._reset_output()
                self.set_warning("Input is neither Neutral text nor ASCII. Expected either "
                                 "'MNEMONIC(operand);' rungs or SOR/EOR tokens.")
                self.status.config(text="No conversion performed.")
                return

            self.detect_label.config(text=f"{result.kind} detected", foreground="#0a6")
            self.output_label.config(text=f"{result.output_kind}")
            self.current_output = result.output
            self.current_input_lines = result.input_lines
            self.current_output_lines = result.output_lines
            self.current_neutral_lines = result.neutral_lines
            self.current_comments = result.comments
            self.current_warnings = result.warnings
            # The copied input is one line with no comments: plain text cannot
            # carry them. Only a saved .shs does.
            self.current_input_single = result.input_single
            output_display = "\n".join(result.output_lines)
            self.output_pane.set(output_display, keep_view=True)

            if reformat:
                self.set_input_text("\n".join(result.input_lines))

            # Highlight from the raw pane text, so the input pane stays correct
            # even mid-edit when it has not been reformatted to one rung per line.
            self.output_pane.apply_spans(
                highlight_spans(output_display, result.output_kind))
            self.input_pane.apply_spans(
                highlight_spans(self.input_pane.get(), result.kind))

            # set_input_text wipes tags, so re-apply the find highlight afterwards.
            self.refresh_find_highlight()

            self.set_warning("  ".join(result.warnings))
            self.status.config(
                text=f"{result.rung_count} rung(s)  •  "
                     f"{result.kind} → {result.output_kind}  •  "
                     f"output is {len(result.output)} chars on a single line"
            )
            self.update_match_count()

        def _reset_output(self):
            self.output_label.config(text="")
            self.output_pane.set("")
            self.output_pane.apply_spans([])
            self.input_pane.apply_spans([])
            self.current_output = ""
            self.current_input_single = ""
            self.current_input_lines = []
            self.current_output_lines = []
            self.current_neutral_lines = []
            self.current_comments = []
            self.current_warnings = []

        def set_input_text(self, value: str):
            self._suppress = True
            try:
                self.input_pane.set(value)
            finally:
                self._suppress = False

        def set_warning(self, msg: str, error: bool = False):
            if msg:
                self.warn_label.config(
                    text=("✖  " if error else "⚠  ") + msg,
                    bg=ERR_BG if error else WARN_BG,
                    fg=ERR_FG if error else WARN_FG,
                )
                self.warn_label.pack(fill="x", pady=(4, 0), before=self.status)
            else:
                self.warn_label.pack_forget()

        # -- commands -----------------------------------------------------
        def format_input(self):
            if self.current_input_lines:
                self.set_input_text("\n".join(self.current_input_lines))
                self.status.config(text="Input reformatted to one rung per line (display only).")
            else:
                self.do_convert(reformat=True)

        def swap(self):
            if not self.current_output:
                return
            # The display form, so the comments come across too.
            self.set_input_text("\n".join(self.current_output_lines))
            self.do_convert(reformat=True)

        def clear(self):
            self.set_input_text("")
            self.do_convert()

        def _to_clipboard(self, value: str, what: str):
            if not value:
                self.status.config(
                    text="Nothing to copy — the last conversion failed, so there is "
                         "no result. Fix the input and try again."
                    if self.last_error else "Nothing to copy."
                )
                return
            self.clipboard_clear()
            self.clipboard_append(value)
            self.update_idletasks()
            self.status.config(
                text=f"Copied {what} to clipboard: {len(value)} chars, single line, "
                     f"no newlines — safe to paste into Studio."
            )

        def copy_output(self):
            self._to_clipboard(self.current_output, "output")

        def copy_input(self):
            self._to_clipboard(self.current_input_single, "input")

        def load_file(self):
            path = filedialog.askopenfilename(
                title="Load rungs",
                filetypes=[("Rungs", "*.shs *.txt *.l5k *.L5K"),
                           ("Studio rung scrap", "*.shs"),
                           ("Text files", "*.txt *.l5k *.L5K"), ("All files", "*.*")],
            )
            if path:
                self.load_path(path)

        def load_path(self, path: str) -> bool:
            """Load rungs from a text file or a Studio rung scrap (.shs)."""
            try:
                with open(path, "rb") as fh:
                    data = fh.read()
            except OSError as exc:
                messagebox.showerror(APP_TITLE, f"Could not read file:\n{exc}")
                return False
            notes: list[str] = []
            if is_shs(data):        # by content, not extension
                try:
                    text, notes = shs_pane_text(data)
                except ConversionError as exc:
                    messagebox.showerror(APP_TITLE, f"Could not read {path}:\n{exc}")
                    return False
            else:
                text = data.decode("utf-8-sig", errors="replace")
            self.set_input_text(text)
            self.do_convert(reformat=True)
            if notes and not self.last_error:
                self.set_warning("  ".join(notes + self.current_warnings))
            if is_shs(data) and not self.last_error:
                n_comments = sum(c is not None for c in self.current_comments)
                self.status.config(
                    text=f"Loaded {len(self.current_neutral_lines)} rung(s) and "
                         f"{n_comments} comment(s) from {path}")
            else:
                self.status.config(text=f"Loaded {path}")
            return True

        def save_file(self):
            if not self.current_output:
                messagebox.showinfo(APP_TITLE, "There is no output to save.")
                return
            path = filedialog.asksaveasfilename(
                title="Save output (single line, Studio-safe)",
                defaultextension=".txt",
                filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            )
            if not path:
                return
            try:
                # Written with no trailing newline: a trailing newline would be
                # selected along with the text and become an empty rung on paste.
                with open(path, "w", encoding="utf-8", newline="") as fh:
                    fh.write(self.current_output)
            except OSError as exc:
                messagebox.showerror(APP_TITLE, f"Could not write file:\n{exc}")
                return
            self.status.config(text=f"Saved single-line output to {path}")

        def save_shs(self):
            if not self.current_neutral_lines:
                messagebox.showinfo(APP_TITLE, "There are no rungs to save.")
                return
            path = filedialog.asksaveasfilename(
                title="Save rungs and comments for Studio (.shs)",
                defaultextension=".shs", initialfile="Converted rungs.shs",
                filetypes=[("Studio rung scrap", "*.shs"), ("All files", "*.*")],
            )
            if path:
                self.save_shs_to(path)

        def save_shs_to(self, path: str) -> bool:
            """Write the rungs, in Neutral form, with their comments as a scrap file
            that can be dragged into a Studio routine."""
            try:
                data = build_shs(self.current_neutral_lines, self.current_comments)
                with open(path, "wb") as fh:
                    fh.write(data)
            except ConversionError as exc:
                messagebox.showerror(APP_TITLE, str(exc))
                return False
            except OSError as exc:
                messagebox.showerror(APP_TITLE, f"Could not write file:\n{exc}")
                return False
            n_comments = sum(c is not None for c in self.current_comments)
            self.status.config(
                text=f"Saved {len(self.current_neutral_lines)} rung(s) and {n_comments} "
                     f"comment(s) to {path} — drag the file into a routine in Studio.")
            return True

        def apply_wrap(self):
            self.input_pane.set_wrap(self.wrap_var.get())
            self.output_pane.set_wrap(self.wrap_var.get())

        def toggle_wrap(self):
            self.wrap_var.set(not self.wrap_var.get())
            self.apply_wrap()
            self.status.config(
                text=f"Word wrap {'on' if self.wrap_var.get() else 'off'}."
            )

        # -- syntax colours -------------------------------------------------
        def _apply_syntax_colors(self, persist: bool = True):
            self.input_pane.configure_syntax(self.syntax_colors)
            self.output_pane.configure_syntax(self.syntax_colors)
            if persist and not save_syntax_colors(self.syntax_colors):
                self.status.config(
                    text=f"Colours applied, but could not be saved to "
                         f"{settings_path()}")

        def open_color_settings(self):
            win = tk.Toplevel(self)
            win.title("Syntax colours")
            win.transient(self.winfo_toplevel())
            win.resizable(False, False)

            body = ttk.Frame(win, padding=14)
            body.pack(fill="both", expand=True)
            ttk.Label(
                body,
                text="Tags and ordinary operands use the pane's normal text colour.",
                foreground="#555",
            ).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 12))

            swatches = {}

            def choose(category):
                initial = self.syntax_colors[category]
                _rgb, picked = colorchooser.askcolor(
                    color=initial, parent=win,
                    title=f"{SYNTAX_LABELS[category]} colour")
                if picked:
                    self.syntax_colors[category] = picked
                    swatches[category].config(background=picked)
                    hex_vars[category].set(picked)
                    self._apply_syntax_colors()

            def commit_hex(category):
                value = hex_vars[category].get().strip()
                if not value.startswith("#"):
                    value = "#" + value
                if HEX_COLOR.match(value):
                    self.syntax_colors[category] = value
                    swatches[category].config(background=value)
                    hex_vars[category].set(value)
                    self._apply_syntax_colors()
                else:
                    hex_vars[category].set(self.syntax_colors[category])

            hex_vars = {}
            for row, category in enumerate(SYNTAX_CATEGORIES, start=1):
                color = self.syntax_colors[category]
                sw = tk.Label(body, background=color, width=4, relief="solid",
                              borderwidth=1, cursor="hand2")
                sw.grid(row=row, column=0, padx=(0, 10), pady=4, sticky="ns")
                sw.bind("<Button-1>", lambda _e, c=category: choose(c))
                swatches[category] = sw

                ttk.Label(body, text=SYNTAX_LABELS[category]).grid(
                    row=row, column=1, sticky="w", pady=4)
                ttk.Label(body, text=SYNTAX_HINTS[category], foreground="#777",
                          font=("Consolas", 9)).grid(
                    row=row, column=2, sticky="w", padx=(14, 14), pady=4)

                var = tk.StringVar(value=color)
                hex_vars[category] = var
                entry = ttk.Entry(body, textvariable=var, width=10,
                                  font=("Consolas", 9))
                entry.grid(row=row, column=3, pady=4)
                entry.bind("<Return>", lambda _e, c=category: commit_hex(c))
                entry.bind("<FocusOut>", lambda _e, c=category: commit_hex(c))

            def reset():
                self.syntax_colors = dict(DEFAULT_SYNTAX_COLORS)
                for category in SYNTAX_CATEGORIES:
                    swatches[category].config(
                        background=self.syntax_colors[category])
                    hex_vars[category].set(self.syntax_colors[category])
                self._apply_syntax_colors()

            buttons = ttk.Frame(body)
            buttons.grid(row=len(SYNTAX_CATEGORIES) + 1, column=0, columnspan=4,
                         sticky="ew", pady=(14, 0))
            ttk.Button(buttons, text="Reset to defaults",
                       command=reset).pack(side="left")
            ttk.Button(buttons, text="Close",
                       command=win.destroy).pack(side="right")

            win.bind("<Escape>", lambda _e: win.destroy())
            win.update_idletasks()
            root = self.winfo_toplevel()
            win.geometry(f"+{root.winfo_rootx() + 90}+{root.winfo_rooty() + 90}")
            return win

        def show_about(self):
            messagebox.showinfo(
                APP_TITLE,
                f"{APP_TITLE}\n\n"
                "Converts Rockwell Studio 5000 ladder rungs between Neutral text and "
                "ASCII, in both directions.\n\n"
                "Copy and Save always emit a single line with no newlines, because "
                "Studio turns every newline in pasted ASCII into a rung break.\n\n"
                f"Known instruction set: {len(INSTRUCTION_SET)} mnemonics — "
                f"{len(KNOWN_INSTRUCTIONS)} from 1756-RM014D-EN-P pp. 210-221, plus the "
                f"{len(V36_MNEMONIC_UPDATES)} renamed in Logix Designer v36"
                + (f", plus {len(AOI_INSTRUCTIONS)} user AOI(s)." if AOI_INSTRUCTIONS else ".")
            )

        def run_selftest(self):
            passed, total, failures = selftest(verbose=False)
            if failures:
                messagebox.showerror(
                    "Self-test FAILED",
                    f"{passed}/{total} checks passed.\n\n" + "\n\n".join(failures[:6]),
                )
            else:
                messagebox.showinfo("Self-test", f"All {total} checks passed.")

        # -- find & replace (input pane only) -----------------------------
        def toggle_find(self, show: bool | None = None):
            show = (not self.find_visible) if show is None else show
            if show:
                if not self.find_visible:
                    self.findbar.pack(fill="x", pady=(0, 4),
                                      after=self.children_toolbar())
                    self.find_visible = True
                # Ctrl+F on an already-open bar focuses the field, as editors do.
                self.find_entry.focus_set()
                self.find_entry.selection_range(0, "end")
                self.update_match_count()
            elif not show and self.find_visible:
                self.findbar.pack_forget()
                self.find_visible = False
                self.input_pane.text.tag_remove("sel", "1.0", "end")
                self.input_pane.highlight_matches([])
                self.output_pane.highlight_matches([])
                self.input_pane.text.focus_set()

        def children_toolbar(self):
            return self.winfo_children()[0]

        HISTORY_LIMIT = 20

        def _remember(self, var, history: list[str], combo) -> None:
            """Push the current field value onto its history, most recent first."""
            value = var.get()
            if not value:
                return
            if value in history:
                history.remove(value)
            history.insert(0, value)
            del history[self.HISTORY_LIMIT:]
            combo.config(values=list(history))

        def _remember_find(self) -> None:
            self._remember(self.find_var, self.find_history, self.find_combo)

        def _remember_replace(self) -> None:
            self._remember(self.find_var, self.find_history, self.find_combo)
            self._remember(self.repl_var, self.repl_history, self.repl_combo)

        def _pattern(self) -> re.Pattern | None:
            needle = self.find_var.get()
            if not needle:
                return None
            pat = re.escape(needle)
            if self.word_var.get():
                pat = r"(?<!\w)" + pat + r"(?!\w)"
            flags = 0 if self.case_var.get() else re.IGNORECASE
            return re.compile(pat, flags)

        def _skip_ranges(self, text: str) -> list[tuple[int, int]]:
            """(start, end) of every comment line, unless Include comments is on."""
            if self.comments_var.get():
                return []
            return [(m.start(), m.end()) for m in COMMENT_LINE.finditer(text)]

        @staticmethod
        def _skipped(pos: int, skips: list[tuple[int, int]], starts: list[int]) -> bool:
            k = bisect.bisect_right(starts, pos) - 1
            return k >= 0 and pos < skips[k][1]

        def _matches(self, pat, text: str, pos: int = 0):
            """Matches of `pat` in `text` from `pos` on, skipping comment lines
            unless Include comments is ticked."""
            skips = self._skip_ranges(text)
            starts = [s for s, _ in skips]
            for m in pat.finditer(text, pos):
                if not self._skipped(m.start(), skips, starts):
                    yield m

        def update_match_count(self):
            if not self.find_visible:
                return
            pat = self._pattern()
            if pat is None:
                self.match_label.config(text="")
                self.refresh_find_highlight()
                return
            n = sum(1 for _ in self._matches(pat, self.input_pane.get()))
            self.match_label.config(text=f"{n} match{'' if n == 1 else 'es'}")
            self.refresh_find_highlight()

        def _match_ranges(self, pat, text: str):
            """(line, start_col, end_col) for every match, clamped to its own line."""
            line_starts = [0]
            for idx, ch in enumerate(text):
                if ch == "\n":
                    line_starts.append(idx + 1)
            ranges = []
            for m in self._matches(pat, text):
                line = bisect.bisect_right(line_starts, m.start()) - 1
                base = line_starts[line]
                line_end = (line_starts[line + 1] - 1 if line + 1 < len(line_starts)
                            else len(text))
                ranges.append((line + 1, m.start() - base, min(m.end(), line_end) - base))
            return ranges

        def refresh_find_highlight(self):
            """Grey every match of the current find term, in BOTH panes.

            The input pane also carries the blue selection marking the active match,
            which is the one Replace acts on. The output pane is read-only, so it
            gets grey only -- there is no active match to distinguish there.

            Because the two formats spell structure differently, a term matching in
            one pane need not match in the other: ';' appears only in Neutral,
            'EOR' only in ASCII. Tag and operand names appear in both.
            """
            pat = self._pattern() if self.hilite_var.get() and self.find_visible else None
            if pat is None:
                self.input_pane.highlight_matches([])
                self.output_pane.highlight_matches([])
                return
            self.input_pane.highlight_matches(
                self._match_ranges(pat, self.input_pane.get()))
            self.output_pane.highlight_matches(
                self._match_ranges(pat, self.output_pane.get()))

        def find_next(self):
            pat = self._pattern()
            if pat is None:
                return
            self._remember_find()
            text = self.input_pane.get()
            widget = self.input_pane.text
            start = widget.index("insert")
            offset = len(widget.get("1.0", start))
            m = next(self._matches(pat, text, offset), None)
            if m is None:
                m = next(self._matches(pat, text, 0), None)    # wrap around
            if m is None:
                self.status.config(text="No matches.")
                return
            s = f"1.0+{m.start()}c"
            e = f"1.0+{m.end()}c"
            self.refresh_find_highlight()
            widget.tag_remove("sel", "1.0", "end")
            widget.tag_add("sel", s, e)
            widget.mark_set("insert", e)
            widget.see(s)
            widget.focus_set()

        def replace_one(self):
            pat = self._pattern()
            if pat is None:
                return
            self._remember_replace()
            widget = self.input_pane.text
            try:
                sel_start, sel_end = widget.index("sel.first"), widget.index("sel.last")
            except tk.TclError:
                self.find_next()
                return
            selected = widget.get(sel_start, sel_end)
            text = self.input_pane.get()
            skips = self._skip_ranges(text)
            in_comment = self._skipped(len(widget.get("1.0", sel_start)), skips,
                                       [s for s, _ in skips])
            if pat.fullmatch(selected) and not in_comment:
                widget.delete(sel_start, sel_end)
                widget.insert(sel_start, self.repl_var.get())
                self.on_input_change()
            self.find_next()

        def replace_all(self):
            pat = self._pattern()
            if pat is None:
                return
            self._remember_replace()
            text = self.input_pane.get()
            repl = self.repl_var.get().replace("\\", "\\\\")
            # Replace only between the skipped comment lines (all of the text when
            # Include comments is on). A match never spans a line, so it never
            # straddles a comment boundary.
            pieces: list[str] = []
            count, last = 0, 0
            for start, end in self._skip_ranges(text) + [(len(text), len(text))]:
                segment, n = pat.subn(repl, text[last:start])
                pieces.append(segment)
                pieces.append(text[start:end])
                count += n
                last = end
            new = "".join(pieces)
            if count:
                self.set_input_text(new)
                self.do_convert()
            self.status.config(text=f"Replaced {count} occurrence{'' if count == 1 else 's'} "
                                    f"in the input pane.")
            self.update_match_count()

    root = tk.Tk()
    root.title(APP_TITLE)
    root.geometry("1400x780")
    try:
        ttk.Style().theme_use("vista")
    except Exception:
        pass
    App(root)
    root.mainloop()


# ---------------------------------------------------------------------------
def _ensure_console() -> None:
    """A --windowed PyInstaller build has no stdout, so --selftest would run
    silently. Re-attach to the console that launched us, if there is one."""
    if sys.stdout is not None:
        return
    try:
        import ctypes

        ATTACH_PARENT_PROCESS = -1
        if ctypes.windll.kernel32.AttachConsole(ATTACH_PARENT_PROCESS):
            sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace")
            sys.stderr = sys.stdout
    except Exception:
        pass    # No console to attach to; the exit code still reports the result.


def main(argv: list[str]) -> int:
    if "--selftest" in argv:
        _ensure_console()
        _, _, failures = selftest(verbose=True)
        return 1 if failures else 0
    if "--help" in argv or "-h" in argv:
        _ensure_console()
        print(__doc__)
        return 0
    launch_gui()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
