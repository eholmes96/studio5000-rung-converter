#!/usr/bin/env python3
"""
GUI regression tests for the Studio 5000 rung converter.

    python test_gui.py            # run them all, exit 0 on success

Complements `python converter.py --selftest`, which covers the parser and
emitters. This file covers the Tkinter layer: the panes, gutter, find & replace,
accelerators, syntax colouring, and the failure paths.

It builds the real application, drives it through its own methods and synthetic
events, then closes it -- there is no mocking of the widget layer. A window will
flash on screen for a second or two; that is expected.

Requires no test framework and no network access.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import traceback
import tkinter as tk

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import converter                                            # noqa: E402

FAILURES: list[str] = []
PASSED = 0
_section = ""


def section(title: str) -> None:
    global _section
    _section = title
    print(f"\n{title}")


def check(label: str, condition, detail="") -> None:
    global PASSED
    ok = bool(condition)
    if ok:
        PASSED += 1
    else:
        FAILURES.append(f"{_section} :: {label}" + (f"  ({detail})" if detail else ""))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" -> {detail}" if detail else ""))


def find_app(root):
    return [w for w in root.winfo_children()
            if w.winfo_class() in ("TFrame", "Frame")][0]


def tags_at(pane, word):
    """Syntax tags covering the first occurrence of `word` in a pane."""
    idx = pane.text.search(word, "1.0", "end")
    if not idx:
        return None
    return sorted(t for t in pane.text.tag_names(idx)
                  if t in converter.SYNTAX_CATEGORIES)


def run_tests(root):
    app = find_app(root)
    root.deiconify()
    root.update()

    # ---------------------------------------------------------------- defaults
    section("Startup defaults")
    check("find bar open by default", app.findbar.winfo_ismapped() == 1)
    check("word wrap on by default", app.wrap_var.get() is True)
    check("panes actually wrapping",
          str(app.input_pane.text.cget("wrap")) == "word"
          and str(app.output_pane.text.cget("wrap")) == "word",
          app.input_pane.text.cget("wrap"))
    check("highlight all on by default", app.hilite_var.get() is True)
    check("sync scroll on by default", app.sync_var.get() is True)
    check("find history starts empty", app.find_combo.cget("values") in ("", ()))
    check("detection label starts blank", app.detect_label.cget("text") == "—",
          app.detect_label.cget("text"))

    # ------------------------------------------------------------- conversion
    section("Conversion and display")
    app.set_input_text(converter.CANON_NEUTRAL)
    app.do_convert(reformat=True)
    root.update()
    nrungs = converter.CANON_ASCII.count("EOR")
    check("detects Neutral", app.detect_label.cget("text") == "Neutral detected",
          app.detect_label.cget("text"))
    check("output label reads ASCII", app.output_label.cget("text") == "ASCII")
    check("output is canonical ASCII", app.current_output == converter.CANON_ASCII)
    check("output has no newline", "\n" not in app.current_output)
    check("output pane shows one rung per line",
          int(app.output_pane.text.index("end-1c").split(".")[0]) == nrungs)
    check("input pane reformatted to one rung per line",
          int(app.input_pane.text.index("end-1c").split(".")[0]) == nrungs)
    check("no warning banner for clean input", app.warn_label.winfo_ismapped() == 0)

    section("Rung gutter")
    app.output_pane._redraw_gutter()
    root.update()
    nums = [app.output_pane.gutter.itemcget(i, "text")
            for i in app.output_pane.gutter.find_all()]
    check("numbers rungs from 0", nums[:3] == ["0", "1", "2"], nums[:4])
    check("one number per rung", len(nums) == nrungs, len(nums))

    section("Clipboard is always the single-line form")
    app.copy_output()
    root.update()
    check("copies canonical ASCII", root.clipboard_get() == converter.CANON_ASCII)
    check("clipboard has no newline", "\n" not in root.clipboard_get())
    app.copy_input()
    root.update()
    check("copy input gives single-line neutral",
          root.clipboard_get() == converter.CANON_NEUTRAL)

    section("Swap")
    app.set_input_text(converter.CANON_NEUTRAL)
    app.do_convert(reformat=True)
    app.swap()
    root.update()
    check("swap flips to ASCII", app.detect_label.cget("text") == "ASCII detected",
          app.detect_label.cget("text"))
    check("swap output is canonical Neutral",
          app.current_output == converter.CANON_NEUTRAL)

    # -------------------------------------------------------- find & replace
    section("Find and replace (input pane only)")
    app.set_input_text(converter.CANON_NEUTRAL)
    app.do_convert(reformat=True)
    app.toggle_find(True)
    app.find_var.set("QuickStopOK")
    app.update_match_count()
    root.update()
    check("match counter", app.match_label.cget("text") == "2 matches",
          app.match_label.cget("text"))

    app.case_var.set(True)
    app.find_var.set("quickstopok")
    app.update_match_count()
    check("case-sensitive finds none", app.match_label.cget("text") == "0 matches",
          app.match_label.cget("text"))
    app.case_var.set(False)

    app.find_var.set("Zone")
    app.word_var.set(True)
    app.update_match_count()
    whole = app.match_label.cget("text")
    app.word_var.set(False)
    app.update_match_count()
    partial = app.match_label.cget("text")
    check("whole-word narrows the match count", whole != partial, f"{whole} vs {partial}")

    app.find_var.set("QuickStopOK")
    app.find_next()
    root.update()
    check("find next selects a match", bool(app.input_pane.text.tag_ranges("sel")))
    check("selection is the search term",
          app.input_pane.text.get("sel.first", "sel.last") == "QuickStopOK",
          app.input_pane.text.get("sel.first", "sel.last"))

    app.repl_var.set("QuickStopOKX")
    app.replace_all()
    root.update()
    check("replace all reaches the output",
          app.current_output.count("QuickStopOKX") == 2,
          app.current_output.count("QuickStopOKX"))
    check("replace all adds no newlines", "\n" not in app.current_output)

    section("Find history dropdown")
    app.find_var.set("AlphaTerm"); app.find_next()
    app.find_var.set("BetaTerm"); app.find_next()
    root.update()
    values = list(app.find_combo.cget("values"))
    check("most recent first", values[0] == "BetaTerm", values[:3])
    app.find_var.set("AlphaTerm"); app.find_next()
    values = list(app.find_combo.cget("values"))
    check("re-use moves to front without duplicating",
          values[0] == "AlphaTerm" and values.count("AlphaTerm") == 1, values[:3])
    app.find_var.set(""); app.find_next()
    check("empty term not recorded", "" not in app.find_combo.cget("values"))
    for i in range(30):
        app.find_var.set(f"term{i}")
        app.find_next()
    check("history capped at 20", len(app.find_combo.cget("values")) == 20,
          len(app.find_combo.cget("values")))
    check("Down arrow opens the dropdown",
          "Post" in str(root.tk.call("bind", "TCombobox", "<Down>")))

    section("Highlight all")
    app.set_input_text("XIC(Pump_Run)OTE(Pump_Run);XIC(Other)OTE(Pump_Run);")
    app.do_convert(reformat=True)
    app.hilite_var.set(True)
    app.find_var.set("Pump_Run")
    app.update_match_count()
    root.update()
    ranges = app.input_pane.text.tag_ranges(converter.TAG_FIND_ALL)
    check("all occurrences tagged", len(ranges) // 2 == 3, len(ranges) // 2)

    app.find_next()
    root.update()
    sel = app.input_pane.text.tag_ranges("sel")
    check("active match still uses the blue selection", len(sel) == 2, len(sel))
    check("selection sits on a highlighted occurrence",
          app.input_pane.text.get("sel.first", "sel.last") == "Pump_Run",
          app.input_pane.text.get("sel.first", "sel.last"))
    check("sel has priority over the grey tag",
          app.input_pane.text.tag_names().index("sel")
          > app.input_pane.text.tag_names().index(converter.TAG_FIND_ALL))

    before = app.input_pane.get()
    app.repl_var.set("Pump_Go")
    app.replace_one()
    root.update()
    after = app.input_pane.get()
    check("Replace changes exactly one occurrence",
          after.count("Pump_Go") == 1 and after.count("Pump_Run") == 2,
          f"{after.count('Pump_Go')} replaced, {after.count('Pump_Run')} left")

    app.hilite_var.set(False)
    app.refresh_find_highlight()
    root.update()
    check("unchecking clears the grey highlight in the input pane",
          len(app.input_pane.text.tag_ranges(converter.TAG_FIND_ALL)) == 0)
    check("unchecking clears the grey highlight in the output pane",
          len(app.output_pane.text.tag_ranges(converter.TAG_FIND_ALL)) == 0)
    app.hilite_var.set(True)

    section("Highlight all reaches the output pane too")
    app.set_input_text("XIC(Pump_Run)OTE(Pump_Run);XIC(Other)OTE(Pump_Run);")
    app.do_convert(reformat=True)
    app.find_var.set("Pump_Run")
    app.update_match_count()
    root.update()
    out_ranges = app.output_pane.text.tag_ranges(converter.TAG_FIND_ALL)
    check("output pane grey-highlights every match", len(out_ranges) // 2 == 3,
          len(out_ranges) // 2)
    check("output highlight covers the right text",
          app.output_pane.text.get(out_ranges[0], out_ranges[1]) == "Pump_Run",
          app.output_pane.text.get(out_ranges[0], out_ranges[1]))
    check("output pane has no blue selection",
          len(app.output_pane.text.tag_ranges("sel")) == 0)

    # The fixture is two rungs, so: 3x Pump_Run, 2x OTE, and 2x EOR in the output.
    # A term that exists in only one format highlights in only one pane.
    app.find_var.set("EOR")
    app.update_match_count()
    root.update()
    check("ASCII-only term highlights in the output pane only",
          len(app.output_pane.text.tag_ranges(converter.TAG_FIND_ALL)) // 2 == 2
          and len(app.input_pane.text.tag_ranges(converter.TAG_FIND_ALL)) == 0,
          (len(app.output_pane.text.tag_ranges(converter.TAG_FIND_ALL)) // 2,
           len(app.input_pane.text.tag_ranges(converter.TAG_FIND_ALL)) // 2))

    app.find_var.set(";")
    app.update_match_count()
    root.update()
    check("Neutral-only term highlights in the input pane only",
          len(app.input_pane.text.tag_ranges(converter.TAG_FIND_ALL)) // 2 == 2
          and len(app.output_pane.text.tag_ranges(converter.TAG_FIND_ALL)) == 0,
          (len(app.input_pane.text.tag_ranges(converter.TAG_FIND_ALL)) // 2,
           len(app.output_pane.text.tag_ranges(converter.TAG_FIND_ALL)) // 2))

    app.find_var.set("OTE")
    app.update_match_count()
    root.update()
    check("a term in both formats highlights in both panes",
          len(app.input_pane.text.tag_ranges(converter.TAG_FIND_ALL)) // 2 == 2
          and len(app.output_pane.text.tag_ranges(converter.TAG_FIND_ALL)) // 2 == 2,
          (len(app.input_pane.text.tag_ranges(converter.TAG_FIND_ALL)) // 2,
           len(app.output_pane.text.tag_ranges(converter.TAG_FIND_ALL)) // 2))

    # Highlighting must survive a re-conversion, which rebuilds the pane text.
    app.find_var.set("Pump_Run")
    app.update_match_count()
    app.do_convert(reformat=True)
    root.update()
    check("output highlight survives a re-conversion",
          len(app.output_pane.text.tag_ranges(converter.TAG_FIND_ALL)) // 2 == 3,
          len(app.output_pane.text.tag_ranges(converter.TAG_FIND_ALL)) // 2)

    check("hiding the find bar clears both panes",
          (app.toggle_find(False),
           len(app.input_pane.text.tag_ranges(converter.TAG_FIND_ALL))
           + len(app.output_pane.text.tag_ranges(converter.TAG_FIND_ALL)))[1] == 0)
    app.toggle_find(True)

    # ------------------------------------------------------- double-click
    section("Double-click selects one token, not the whole rung")
    app.set_input_text("XIC(SafetyZone[2].QuickStopOK)OTE(_Drive1:I.Ready);")
    app.do_convert()
    root.update()
    body = app.input_pane.get()

    def word_at(substring, offset=0):
        col = body.index(substring) + offset
        span = app.input_pane.select_word_at(1, col)
        return body[span[0]:span[1]] if span else None

    check("mnemonic alone", word_at("XIC") == "XIC", word_at("XIC"))
    check("does not swallow the rung", word_at("OTE") == "OTE", word_at("OTE"))
    # Square brackets are delimiters by request, so a subscripted tag selects in
    # three pieces: base name, subscript, member path.
    check("base tag name up to the subscript",
          word_at("SafetyZone") == "SafetyZone",
          word_at("SafetyZone"))
    check("array subscript is its own token", word_at("2].") == "2", word_at("2]."))
    check("member path after the subscript",
          word_at("QuickStopOK") == ".QuickStopOK", word_at("QuickStopOK"))
    check("period is NOT a delimiter (whole dotted path when no subscript)",
          word_at("_Drive1") == "_Drive1:I.Ready", word_at("_Drive1"))
    check("colon is NOT a delimiter", word_at("I.Ready") == "_Drive1:I.Ready",
          word_at("I.Ready"))
    check("clicking a bracket selects just it", word_at("[") == "[", word_at("["))
    check("clicking a paren selects just it", word_at("(") == "(", word_at("("))

    app.set_input_text("SOR CPT Dest \"A / (B *100)\" EOR")
    app.do_convert()
    root.update()
    body = app.input_pane.get()
    check("quote is a delimiter", word_at("A /") == "A", word_at("A /"))
    check("space-separated token", word_at("EOR") == "EOR", word_at("EOR"))

    check("double-click event selects, not the whole line",
          app.input_pane._on_double_click(
              type("E", (), {"x": 5, "y": 5})()) == "break")

    # ----------------------------------------------------------- accelerators
    section("Alt accelerators")
    import tkinter.ttk as ttk_
    labels = {}
    for frame in (app.winfo_children()[0], app.findbar):
        for w in frame.winfo_children():
            if isinstance(w, (ttk_.Button, ttk_.Checkbutton)):
                try:
                    labels[str(w.cget("text"))] = int(w.cget("underline"))
                except Exception:
                    pass
    for text, (letter, idx) in {
        "Copy output (Studio-safe, 1 line)": ("C", 0),
        "Copy input (1 line)": ("T", 9),
        "⇄ Swap": ("S", 2),
        "Format rungs": ("F", 0),
        "Word wrap": ("W", 0),
        "Find next": ("D", 3),
        "Replace": ("R", 0),
        "Replace all": ("A", 8),
    }.items():
        got = labels.get(text)
        check(f"underline on {text!r} marks {letter}",
              got == idx and text[idx].upper() == letter,
              f"index={got}")

    app.set_input_text(converter.CANON_NEUTRAL)
    app.do_convert(reformat=True)
    root.update()
    app.input_pane.text.focus_set()

    wrap_before = app.wrap_var.get()
    app.input_pane.text.event_generate("<Alt-w>")
    root.update()
    check("Alt+W toggles word wrap", app.wrap_var.get() != wrap_before,
          app.wrap_var.get())
    app.input_pane.text.event_generate("<Alt-w>")
    root.update()
    check("Alt+W toggles back", app.wrap_var.get() == wrap_before)

    app.find_var.set("QuickStopOK")
    app.input_pane.text.tag_remove("sel", "1.0", "end")
    app.input_pane.text.mark_set("insert", "1.0")
    app.input_pane.text.event_generate("<Alt-d>")
    root.update()
    check("Alt+D runs Find next", bool(app.input_pane.text.tag_ranges("sel")))

    app.input_pane.text.event_generate("<Alt-s>")
    root.update()
    check("Alt+S runs Swap", app.detect_label.cget("text") == "ASCII detected",
          app.detect_label.cget("text"))

    app.input_pane.text.event_generate("<Alt-c>")
    root.update()
    check("Alt+C copies output", root.clipboard_get() == app.current_output)

    before_len = len(app.input_pane.get())
    app.input_pane.text.event_generate("<Alt-f>")
    root.update()
    check("Alt+F does not type an 'f' into the pane",
          len(app.input_pane.get()) <= before_len + 1)

    # ------------------------------------------------------ syntax colouring
    section("Syntax colouring")
    app.set_input_text(
        'CPT Drive1.Status.Current "_Drive1:I.Out / (Drive1.MotorFLA *100)" EOR '
        'SOR BST XIC Zone[2].QStop NXB Example_Aoi_1 Tag_A BND TON T1 5000 0 EOR'
    )
    app.do_convert(reformat=True)
    root.update()
    for pane, name in ((app.input_pane, "input"), (app.output_pane, "output")):
        check(f"{name}: instruction tagged", tags_at(pane, "CPT") == ["instruction"],
              tags_at(pane, "CPT"))
        check(f"{name}: AOI tagged", tags_at(pane, "Example_Aoi_1") == ["aoi"],
              tags_at(pane, "Example_Aoi_1"))
        check(f"{name}: literal tagged", tags_at(pane, "5000") == ["literal"],
              tags_at(pane, "5000"))
    check("plain tag left uncoloured", tags_at(app.output_pane, "Tag_A") == [],
          tags_at(app.output_pane, "Tag_A"))
    check("expression tagged", tags_at(app.input_pane, "MotorFLA") == ["expression"],
          tags_at(app.input_pane, "MotorFLA"))

    section("Syntax colour settings")
    defaults = converter.DEFAULT_SYNTAX_COLORS
    check("default palette intact",
          defaults["rung"] == "#5F5E5A" and defaults["branch"] == "#534AB7"
          and defaults["instruction"] == "#185FA5" and defaults["literal"] == "#3B6D11")
    app.syntax_colors["instruction"] = "#AA0000"
    app._apply_syntax_colors(persist=False)
    root.update()
    check("recolour applies live to both panes",
          str(app.input_pane.text.tag_cget("instruction", "foreground")) == "#AA0000"
          and str(app.output_pane.text.tag_cget("instruction", "foreground")) == "#AA0000")

    win = app.open_color_settings()
    root.update()
    swatches = [w for w in win.winfo_children()[0].winfo_children()
                if isinstance(w, tk.Label)]
    check("dialog shows one swatch per category",
          len(swatches) == len(converter.SYNTAX_CATEGORIES), len(swatches))
    win.destroy()
    root.update()

    saved_path = converter.settings_path()
    had_settings = os.path.exists(saved_path)
    backup = open(saved_path, encoding="utf-8").read() if had_settings else None
    try:
        check("palette saves", converter.save_syntax_colors(app.syntax_colors) is True)
        check("palette reloads",
              converter.load_syntax_colors()["instruction"] == "#AA0000")
        with open(saved_path, "w", encoding="utf-8") as fh:
            fh.write('{"syntax_colors": {"rung": "not-a-colour", "x": "#fff"}}')
        check("malformed settings fall back to defaults",
              converter.load_syntax_colors() == defaults)
        os.remove(saved_path)
        check("missing settings fall back to defaults",
              converter.load_syntax_colors() == defaults)
    finally:
        if backup is not None:
            with open(saved_path, "w", encoding="utf-8") as fh:
                fh.write(backup)
        elif os.path.exists(saved_path):
            os.remove(saved_path)

    # ------------------------------------------------------- failure handling
    section("A failed conversion never leaves stale output")
    swallowed = []
    root.report_callback_exception = lambda e, v, t: swallowed.append(e.__name__)

    app.set_input_text("XIC(Good_Tag)OTE(Also_Good);")
    app.do_convert(reformat=True)
    root.update()
    good_pane, good_clip = app.output_pane.get(), app.current_output

    app.set_input_text("[" * 1200 + "XIC(bad)" + "]" * 1200 + ";")
    root.after(0, app.do_convert)
    root.update()

    check("nothing escapes the Tk callback", swallowed == [], swallowed)
    check("output pane cleared", app.output_pane.get() == "")
    check("current_output cleared", app.current_output == "")
    check("not showing stale output",
          app.output_pane.get() != good_pane and app.current_output != good_clip)
    check("error banner shown", app.warn_label.winfo_ismapped() == 1)
    check("banner uses error styling", str(app.warn_label.cget("bg")) == "#ffd9d9",
          app.warn_label.cget("bg"))
    check("detect label says failed",
          app.detect_label.cget("text") == "Conversion failed",
          app.detect_label.cget("text"))
    check("last_error recorded", bool(app.last_error))

    root.clipboard_clear(); root.clipboard_append("SENTINEL"); root.update()
    app.copy_output()
    root.update()
    check("copy refuses rather than giving stale rungs",
          root.clipboard_get() == "SENTINEL", root.clipboard_get()[:30])
    check("copy explains why",
          "conversion failed" in app.status.cget("text").lower(),
          app.status.cget("text")[:60])

    for label, text in (("5000 unclosed '['", "[" * 5000 + "XIC(a);"),
                        ("deep BST", "BST " * 1200 + "XIC a EOR")):
        app.set_input_text(text)
        root.after(0, app.do_convert)
        root.update()
        check(f"{label} refused cleanly",
              app.current_output == "" and bool(app.last_error))

    app.set_input_text("XIC(Recovered)OTE(Fine);")
    app.do_convert(reformat=True)
    root.update()
    check("recovers after a failure",
          app.current_output == "XIC Recovered OTE Fine EOR", app.current_output)
    check("last_error cleared", app.last_error is None)
    check("error banner gone", app.warn_label.winfo_ismapped() == 0)

    depth = converter.MAX_BRANCH_DEPTH - 1
    app.set_input_text("[" * depth + "XIC(a)" + "]" * depth + ";")
    app.do_convert()
    root.update()
    check(f"nesting at depth {depth} still converts",
          app.current_output.count("BST") == depth and app.last_error is None,
          app.current_output.count("BST"))

    root.report_callback_exception = lambda e, v, t: None

    section("Warnings stay distinct from errors")
    app.set_input_text("SOR EXAMPLE_AOI Tag_A EOR")
    app.do_convert()
    root.update()
    check("AOI warning banner shows", app.warn_label.winfo_ismapped() == 1)
    check("AOI warning is amber, not red",
          str(app.warn_label.cget("bg")) == "#fff4c2", app.warn_label.cget("bg"))
    check("AOI warning is not a failure", app.last_error is None)
    check("AOI token preserved", "EXAMPLE_AOI(Tag_A)" in app.current_output,
          app.current_output)

    section("Detection edge cases")
    app.set_input_text("just some prose")
    app.do_convert()
    root.update()
    check("unrecognized input", app.detect_label.cget("text") == "Unrecognized",
          app.detect_label.cget("text"))
    app.set_input_text("")
    app.do_convert()
    root.update()
    check("empty input shows em dash", app.detect_label.cget("text") == "—")
    check("clear empties both panes",
          app.input_pane.get() == "" and app.current_output == "")

    # ---------------------------------------------------------- rung comments
    section("Rung comments from a Studio scrap file (.shs)")
    tmp = tempfile.mkdtemp(prefix="rungconv-test-")
    try:
        src = os.path.join(tmp, "three rungs.shs")
        with open(src, "wb") as fh:
            fh.write(converter.SHS_FIXTURE)
        check("loads a .shs", app.load_path(src) is True)
        root.update()
        lines = app.input_pane.get().split("\n")
        check("comment lines sit above their rung",
              lines[1] == "// ********" and lines[5] == "NOP();"
              and lines[6] == "// RESET ON THE FALLING EDGE.", lines[:8])
        check("status counts rungs and comments",
              "3 rung(s) and 2 comment(s)" in app.status.cget("text"),
              app.status.cget("text")[:60])
        for pane, name in ((app.input_pane, "input"), (app.output_pane, "output")):
            check(f"{name}: comment coloured", tags_at(pane, "AREA 1") == ["comment"],
                  tags_at(pane, "AREA 1"))
        check("comments are dark green by default",
              str(app.input_pane.text.tag_cget("comment", "foreground")).lower()
              == converter.DEFAULT_SYNTAX_COLORS["comment"].lower() == "#1b5e20")
        check("output pane shows the comments too",
              "// AREA 1 SAFETY GATE" in app.output_pane.get())

        app.input_pane._redraw_gutter()
        root.update()
        gutter = app.input_pane.gutter
        nums = [gutter.itemcget(i, "text") for i in gutter.find_all()]
        check("gutter numbers rungs only, skipping comment lines",
              nums == ["0", "1", "2"], nums)
        nop_y = app.input_pane.text.dlineinfo("6.0")[1]
        one = [i for i in gutter.find_all() if gutter.itemcget(i, "text") == "1"]
        check("rung 1's number is beside NOP(), not its comment",
              one and abs(gutter.coords(one[0])[1] - nop_y) < 2)

        check("copied output carries no comments",
              "//" not in app.current_output and "AREA" not in app.current_output)
        check("copied input carries no comments",
              "//" not in app.current_input_single and "\n" not in app.current_input_single)

        out = os.path.join(tmp, "saved.shs")
        check("saves a .shs", app.save_shs_to(out) is True)
        check("unchanged rungs save byte for byte",
              open(out, "rb").read() == converter.SHS_FIXTURE)

        section("Find & Replace and comments")
        boxes = [w.cget("text") for w in app.findbar.pack_slaves()
                 if w.winfo_class() == "TCheckbutton"]
        check("Include comments sits right of Whole word",
              "Include comments" in boxes
              and boxes.index("Include comments") == boxes.index("Whole word") + 1, boxes)
        check("Include comments off by default", app.comments_var.get() is False)

        app.toggle_find(True)
        app.case_var.set(False); app.word_var.set(False)
        app.find_var.set("gate")
        app.update_match_count()
        check("comment lines not counted when off",
              app.match_label.cget("text") == "6 matches", app.match_label.cget("text"))
        app.comments_var.set(True); app.update_match_count()
        check("comment lines counted when on",
              app.match_label.cget("text") == "8 matches", app.match_label.cget("text"))
        app.comments_var.set(False); app.update_match_count()
        in_ranges = app.input_pane.text.tag_ranges(converter.TAG_FIND_ALL)
        check("highlight all skips comments when off", len(in_ranges) // 2 == 6,
              len(in_ranges) // 2)

        app.input_pane.text.mark_set("insert", "1.0")
        for _ in range(7):
            app.find_next()
        sel_line = app.input_pane.text.get("sel.first linestart", "sel.first lineend")
        check("find next never lands in a comment when off",
              not sel_line.lstrip().startswith("//"), sel_line[:30])

        app.input_pane.text.tag_remove("sel", "1.0", "end")
        app.input_pane.text.tag_add("sel", "3.17", "3.21")     # GATE in a comment
        check("test selects a real match inside a comment",
              app.input_pane.text.get("sel.first", "sel.last") == "GATE",
              app.input_pane.text.get("sel.first", "sel.last"))
        app.repl_var.set("Door")
        before = app.input_pane.get()
        app.replace_one()
        check("replace never touches a selected comment when off",
              "AREA 1 SAFETY GATE" in app.input_pane.get()
              and app.input_pane.get().count("Gate[1]") == before.count("Gate[1]"))

        app.replace_all()
        root.update()
        text_now = app.input_pane.get()
        check("replace all renames the logic",
              text_now.count("Door[1]") == 6 and "Gate[1]" not in text_now,
              text_now.count("Door[1]"))
        check("replace all leaves comments alone when off",
              "// AREA 1 SAFETY GATE" in text_now and "// GATE 1" in text_now)
        app.save_shs_to(out)
        renamed_rungs, renamed_comments, _ = converter.read_shs(open(out, "rb").read())
        check("saved .shs has renamed rungs and the original comments",
              all("Door[1]" in r for r in renamed_rungs if r != "NOP();")
              and renamed_comments == converter.SHS_FIXTURE_COMMENTS)

        app.comments_var.set(True)
        app.find_var.set("GATE 1"); app.repl_var.set("GATE 2")
        app.replace_all()
        root.update()
        check("replace all edits comments when on", "// GATE 2" in app.input_pane.get())
        app.comments_var.set(False)

        section("Swap keeps comments")
        app.load_path(src)
        app.swap()
        root.update()
        check("swap to ASCII keeps the comment lines",
              app.detect_label.cget("text") == "ASCII detected"
              and "// AREA 1 SAFETY GATE" in app.input_pane.get())
        app.save_shs_to(out)
        check("a .shs saved from the ASCII side is byte for byte",
              open(out, "rb").read() == converter.SHS_FIXTURE)
        app.toggle_find(True)
        app.find_var.set("")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    section("Large routine and scroll sync")
    app.set_input_text(converter.CANON_NEUTRAL * 12)
    app.do_convert(reformat=True)
    root.update()
    check("120 rungs convert", app.current_output.count("EOR") == 120,
          app.current_output.count("EOR"))
    app.input_pane.text.yview_moveto(0.5)
    root.update()
    iv = app.input_pane.text.yview()[0]
    ov = app.output_pane.text.yview()[0]
    check("input actually scrolled", iv > 0.3, round(iv, 2))
    check("sync scroll moved the partner", abs(ov - iv) < 0.05, (round(iv, 2), round(ov, 2)))
    app.output_pane._redraw_gutter()
    root.update()
    nums = [app.output_pane.gutter.itemcget(i, "text")
            for i in app.output_pane.gutter.find_all()]
    check("gutter renumbers after scrolling",
          nums and nums[0] != "0"
          and nums == [str(int(nums[0]) + k) for k in range(len(nums))],
          nums[:3])


def main() -> int:
    real_mainloop = tk.Tk.mainloop

    def patched(self, *args):
        try:
            run_tests(self)
        except Exception:
            traceback.print_exc()
            FAILURES.append("UNCAUGHT EXCEPTION IN TEST RUN")
        finally:
            self.destroy()

    tk.Tk.mainloop = patched
    try:
        converter.launch_gui()
    finally:
        tk.Tk.mainloop = real_mainloop

    print("\n" + "-" * 62)
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S), {PASSED} passed:\n")
        for failure in FAILURES:
            print("  " + failure)
        return 1
    print(f"All {PASSED} GUI checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
