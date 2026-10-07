# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Windows desktop tool that converts Rockwell Studio 5000 / Logix Designer ladder rungs between
**Neutral text** (`XIC(a)OTE(b);`) and **ASCII** (`SOR XIC a OTE b EOR`), both directions, with
live auto-detection. Python 3.12 + Tkinter, standard library only at runtime, shipped as a
PyInstaller `--onefile --windowed` exe. No network access and no telemetry — keep it that way.
Git repository, published on GitHub as `eholmes96/studio5000-rung-converter`.

`README.md` is the user-facing manual and the place to look for detail on any feature. This file
covers what you need to work on the code and what the README doesn't say.

This project was handed over from another Claude account; treat this file as the source of
truth. Machine-specific details (folder paths, shortcut locations) are deliberately kept out of
the repo and live in Claude Code's local memory instead.

## Commands

```bash
python converter.py                  # launch the GUI
python converter.py --selftest       # parser/emitter suite, exit 0 = pass
python test_gui.py                   # GUI suite, exit 0 = pass
```

- `test_gui.py` builds the real app and drives it — a window flashes for a few seconds. It needs
  an interactive desktop session. It backs up and restores the user's colour settings file.
- Neither suite can run a single test; each runs everything in one pass. For a quick check, call
  the functions directly:
  `python -c "import converter as c; r = c.convert('XIC(a)OTE(b);'); print(r.kind, r.output, r.warnings)"`
- The self-test is also in the GUI: Help → Run self-test (dev), or Ctrl+Alt+T.
- There is no linter or formatter configured.

Build, from the project folder:

```bash
python -m PyInstaller --noconfirm --noupx --onefile --windowed --name Studio5000RungConverter converter.py
dist/Studio5000RungConverter.exe --selftest
```

`--noupx` keeps the exe unpacked even on a machine that has UPX, because UPX-packed
PyInstaller exes trigger more antivirus false positives. The command regenerates
`Studio5000RungConverter.spec` on every build, so change build options in the command (here
and in README), never by editing the spec. Then delete `build/` and `__pycache__/`. Toolchain
at handover: Python 3.12.10, PyInstaller 6.21.0.

**A change is done when** both suites pass, the exe has been rebuilt, and the rebuilt exe passes
`--selftest`. The user runs the exe through shortcuts, not the source — an earlier session skipped
the rebuild and the user ran a stale exe for weeks without a finished feature.

## Architecture

Everything is in `converter.py`, layered top to bottom:

1. **Config tables** (top of file, meant to be edited): `KNOWN_INSTRUCTIONS` (199 mnemonics
   extracted from the 1756-RM014D-EN-P neutral-text table, pp. 210–221), `V36_MNEMONIC_UPDATES`
   (16 renames), `AOI_INSTRUCTIONS` (ships empty — see below), `EXPRESSION_OPERAND_INDEX`,
   `DEFAULT_SYNTAX_COLORS`, `WORD_DELIMITERS`, `MAX_BRANCH_DEPTH`. `INSTRUCTION_SET` is the union.
2. **Rung model**: a rung is a list of `Instruction(mnemonic, operands)`, `Branch(legs)` or `Raw`.
3. **Parsers**: `_parse_neutral` is a character scanner tracking paren depth, bracket depth and
   `$`-escaped `'string'` literals, so `Tag[1,2]` and `CPT(d,(a+b)*c)` survive. `_parse_ascii`
   tokenizes (a `"quoted run"` is one operand), then each mnemonic greedily consumes operand
   tokens until the next mnemonic or keyword. Both return `(rungs, warnings, spans)`; the public
   `parse_neutral`/`parse_ascii` drop the spans.
4. **Emitters**: `emit_neutral_lines` / `emit_ascii_lines` return one string per rung. The panes
   show them one per line; the clipboard gets them joined into one line.
5. **Pipeline**: `detect()` → `convert()` → a `Conversion` holding the output, per-rung display
   lines for both panes, and warnings.
6. **Syntax highlighting reuses the parsers**: `highlight_spans()` calls the same `_parse_*`
   functions and maps their spans to Tk line/column positions. Colouring always matches how the
   text will actually be converted — that is how an unregistered AOI can be shown in red.
7. **GUI**: `launch_gui()` defines `ProxyText`, `RungPane` and `App` *inside* the function.
   `test_gui.py` depends on that: it patches `tk.Tk.mainloop`, finds the `App` among the root
   window's children, and calls its methods directly.

Flows in `App` that are easy to break:

- Typing triggers `on_input_change` → 250 ms debounce (0 on paste) → `do_convert`. `do_convert`
  wraps `_convert_now` in try/except because Tk silently swallows exceptions in `after()`
  callbacks. On failure `_conversion_failed` clears the output *and* `current_output`, so Copy can
  never hand over the previous input's rungs. Keep that guard.
- `reformat=True` rewrites the input pane as one rung per line. It runs only on paste, load, Swap
  and Format rungs — never while typing, because the caret would jump. The user approved this.
- The rung gutter is a Canvas numbered from 0 using `dlineinfo` per logical line, so it stays
  aligned with word wrap on. `ProxyText` renames the Tk widget command to raise `<<Changed>>` on
  every insert, delete and scroll.
- Find & Replace edits the input pane only. Highlight all greys every match in *both* panes; the
  blue `sel` marks the active match in the input pane, and Replace acts only on that one.

## Rules the output must follow

- **Copy and Save always produce one line with no newline characters**, and saved files have no
  trailing newline. Studio treats every newline in pasted ASCII as a rung break and inserts empty
  rungs. This is the reason the tool exists.
- ASCII: rung 0 has no leading `SOR`; every later rung starts with `SOR`; every rung ends with
  `EOR`. When reading, a rung ends at `EOR` *or* at the next `SOR`, so a missing `EOR` is tolerated.
- Drop at most one trailing empty rung (the END rung Studio adds when copying a whole routine).
  Never drop an empty rung in the middle.
- Neutral → ASCII needs no instruction table. ASCII → Neutral has to guess which tokens are
  instructions: the known set wins, then the shape `^[A-Z]{2,4}[0-9]?$`. `LIMIT`, `TRUNC`,
  `TO_BCD` and `BCD_TO` fail that shape, so they must stay in the known set or they get folded
  into the previous instruction's operands.
- In ASCII, an operand containing whitespace is wrapped in double quotes, and only then.
- Never convert between v35 and v36 mnemonic spellings; reproduce whatever spelling came in.
- Nesting deeper than `MAX_BRANCH_DEPTH` (100) raises `ConversionError`; the emitters are
  recursive and would otherwise exhaust the stack around 994 levels.

## Facts about Studio that aren't in Rockwell's manuals, or where they conflict

- **Expression quoting** — Studio's ASCII copy wraps a `CPT`/`CMP`/`FAL`/`FSC` expression in
  double quotes. The user confirmed this from real Studio output; no manual documents it.
- **v36 renamed 16 mnemonics** (`MOV`→`MOVE`, `GRT`→`GT`, `EQU`→`EQ`, …). The authority is the
  Studio 5000 online help, *Instruction Set → Updated instruction mnemonics*. The 1756-RM014D PDF
  (Sept 2025) still lists only the legacy names. Greater-than is `GT`, not `GR`.
- RM014's branch examples use `[,A B]` — that leading comma is an empty first leg, not required
  syntax. Real Studio output writes `[leg,leg]`. Both parse.

## Working with this user

- A controls engineer who checks results by pasting into a live Studio session. Say clearly when
  something hasn't been checked that way.
- **Custom AOI names are the user's intellectual property.** Never write them into code, README,
  tests, this file or memory — even names you notice in a screenshot or a pasted routine.
  `AOI_INSTRUCTIONS` ships empty on purpose. Renaming is not enough: never put a custom AOI's
  operands, parameters, purpose or behaviour into the repo either. The only non-Rockwell
  instruction names allowed are meaningless placeholders (`Example_Aoi_1`, `Example_Aoi_2`,
  `EXAMPLE_AOI`, `XYZQ`) with meaningless operands (`Tag_A`, `Tag_B`, `A`, `B`).
- **The repo is public, so no plant tag names either.** The canonical fixtures are real Studio
  output with every tag renamed. Rungs the user pastes in carry real tag and AOI names; rename
  them before they go into a fixture, the README, a commit message or an issue.
- Prefers answers researched from Rockwell's own documentation. When a source and real behaviour
  disagree, document it in README's Sources section.
- UI defaults they chose: Find & Replace open; Highlight all, Word wrap and Sync scroll on. Alt
  shortcuts have an underlined letter: **C**opy output, Copy inpu**t**, **S**wap, **F**ormat
  rungs, **W**ord wrap, Fin**d** next, **R**eplace, Replace **a**ll. The menu bar deliberately has
  no Alt mnemonics so Alt+F reaches Format rungs. Double-click selects up to
  `space ( ) [ ] , ; " '`; `.` and `:` are deliberately not delimiters.
- Update README whenever behaviour changes.

## Windows pitfalls

- The working folder is cloud-synced. `rm -rf dist` can delete the exe and then fail on the
  directory, leaving no binary. Let PyInstaller overwrite in place instead.
- If the user has the app open, Windows locks the exe. Don't kill their session: build with a
  temporary `--distpath`, verify there, then rename the running exe and copy the new one in.
- The exe is `--windowed`; `_ensure_console()` re-attaches to the parent console so `--selftest`
  can print.
- The user launches `dist\Studio5000RungConverter.exe` through shortcuts named
  `PLC Text Changer.lnk` (locations in local memory, not here). They store absolute paths, so
  recreate them if the folder or machine changes. `*.lnk` is git-ignored.
- Put permanent tests in this folder. Scripts left in temporary directories are wiped between
  sessions (an 80-check suite was lost that way before `test_gui.py` existed).
- With Git Bash, `sed` replacements containing backslashes can silently drop them. Use an editor
  tool or Python for text with Windows paths in it.

## Git and publishing

- Tracked: the code, both test files, `README.md`, this file and `.gitignore`. Ignored: `dist/`,
  `build/`, `__pycache__/`, the generated `.spec` and `*.lnk`.
- Commits use the repo-local identity `eholmes96` with the GitHub noreply address, never a
  personal email. Check with `git config --local user.email` before committing.
- The exe is never committed. Ship it as a GitHub Release asset.
- Before every commit, grep the diff for plant tag names, AOI names, and anything naming the
  user's employer or a local folder path.

## Open backlog

From a code review in August 2026. All still open at handover.

Worth doing:
- `load_file` has no size limit, and the dialog offers All files. A 25 MB binary freezes the
  window for about 5 s.
- `_convert_now` parses three times per keystroke: `convert()` plus a `highlight_spans()` for
  each pane. About 5 MB of rungs costs roughly 5 s per debounce. Reuse the spans from the first
  parse.

Minor:
- `detect()` strips parenthesised operands at most 4 times, so deeper nesting could leave a
  keyword visible and misdetect the format.
- `_ascii_tokens` splits `Raw` pass-through text on whitespace, though README says it passes
  through untouched.
- `children_toolbar()` assumes the toolbar is `winfo_children()[0]`.
- The self-test computes `total = len(checks) + 3`, which under-reports how many checks ran.
- `ProxyText._proxy` swallows every `TclError`.
- An unterminated `'string` literal swallows the rung's terminating `;`.
- The exe is unsigned, so other machines show a SmartScreen warning on first run.

Not yet checked against a live Studio session:
- Omitting `SOR` on rung 0.
- Leaving space-free expressions unquoted.
- `LIMIT`, `TRUNC`, `TO_BCD` and `BCD_TO` on real v36 routines (the test fixtures are synthetic).

## After a transfer to a new account or machine

- Colour choices are saved per user in `%APPDATA%\Studio5000RungConverter\settings.json`, so a
  new account starts with the default colours.
- On a new machine: `pip install pyinstaller`, then recreate the shortcuts. A new Claude
  account on the same machine needs neither.
- `pypdf` was used once to extract the instruction list from the RM014 PDF. The app doesn't need it.
