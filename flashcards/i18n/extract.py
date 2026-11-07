"""Collect every interface string and rebuild en.json; report what other languages are missing.

    python3 flashcards/i18n/extract.py          # write en.json, list missing keys per language
    python3 flashcards/i18n/extract.py --check  # only check (exit 1 if anything is missing)
    python3 flashcards/i18n/extract.py --verify fr   # full check of one language file

Strings are found with Python's own parser: the first argument of t("..."), the first two of
tn("...", "...", n), and N_("...") (marks a string that is translated later with t(variable)).
"""
import ast, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SOURCES = ["app.py", "flashcards", "bundled_addons"]


def source_files():
    for s in SOURCES:
        p = os.path.join(ROOT, s)
        if os.path.isfile(p):
            yield p
            continue
        for dirpath, dirnames, files in os.walk(p):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for f in sorted(files):
                if f.endswith(".py"):
                    yield os.path.join(dirpath, f)


def _name(func):
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _const(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def scan():
    """Returns (singles, plurals, problems). singles: set of keys; plurals: {singular: plural}."""
    singles, plurals, problems = set(), {}, []
    for path in source_files():
        rel = os.path.relpath(path, ROOT)
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), rel)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            name = _name(node.func)
            if name in ("t", "N_"):
                key = _const(node.args[0])
                if key is not None:
                    singles.add(key)
                elif isinstance(node.args[0], ast.JoinedStr):
                    problems.append(f"{rel}:{node.lineno}: {name}() with an f-string; use {{name}} placeholders")
            elif (name == "tn" and len(node.args) >= 3) or (name == "plural_table" and len(node.args) == 2):
                # plural_table(one, many): a plural message for the browser (flashcards/offline.py)
                one, many = _const(node.args[0]), _const(node.args[1])
                if one is not None and many is not None:
                    plurals[one] = many
                elif any(isinstance(a, ast.JoinedStr) for a in node.args[:2]):
                    problems.append(f"{rel}:{node.lineno}: tn() with an f-string; use {{n}} placeholders")
    return singles, plurals, problems


def english():
    singles, plurals, problems = scan()
    en = {k: k for k in sorted(singles - set(plurals))}
    for one in sorted(plurals):
        en[one] = [one, plurals[one]]
    return dict(sorted(en.items())), problems


def missing(code, en):
    path = os.path.join(HERE, f"{code}.json")
    try:
        with open(path, encoding="utf-8") as f:
            cat = json.load(f)
    except (OSError, ValueError):
        cat = {}
    return [k for k in en if k not in cat]


def problems_for(code, en):
    """Everything wrong with one language file: missing/extra keys, plural forms, placeholders."""
    import string
    from flashcards.i18n import PLURAL_FORMS
    fields = lambda s: sorted({f[1] for f in string.Formatter().parse(s) if f[1] is not None})
    path = os.path.join(HERE, f"{code}.json")
    try:
        with open(path, encoding="utf-8") as f:
            cat = json.load(f)
    except OSError:
        return [f"{code}: no file {code}.json"]
    except ValueError as e:
        return [f"{code}: not valid JSON: {e}"]
    bad = []
    for key, value in en.items():
        got = cat.get(key)
        if got is None:
            bad.append(f"{code}: missing {key!r}")
        elif isinstance(value, list):
            if not (isinstance(got, list) and len(got) == PLURAL_FORMS[code]
                    and all(isinstance(x, str) and x for x in got)):
                bad.append(f"{code}: {key!r} needs a list of {PLURAL_FORMS[code]} non-empty plural forms")
            else:
                for x in got:
                    try:
                        extra = set(fields(x)) - set(fields(value[1])) - {"n"}
                    except ValueError:
                        extra = {"<bad braces>"}
                    if extra:
                        bad.append(f"{code}: {key!r} form {x!r} has unknown placeholders {sorted(extra)}")
        elif not (isinstance(got, str) and got):
            bad.append(f"{code}: {key!r} should be non-empty text")
        else:
            try:
                same = fields(got) == fields(key)
            except ValueError:
                same = False
            if not same:
                bad.append(f"{code}: {key!r} -> {got!r}: placeholders must be exactly {fields(key)}")
    extra = sorted(set(cat) - set(en))
    if extra:
        bad.append(f"{code}: {len(extra)} keys not in en.json, e.g. {extra[:3]}")
    return bad


def main():
    sys.path.insert(0, ROOT)
    from flashcards.i18n import LANGUAGES
    if "--verify" in sys.argv:
        en, _ = english()
        code = sys.argv[sys.argv.index("--verify") + 1]
        bad = problems_for(code, en)
        print("\n".join(bad[:60]) + (f"\n... {len(bad)} problems" if bad else f"{code}.json OK ({len(en)} strings)"))
        return 1 if bad else 0
    en, problems = english()
    for p in problems:
        print("PROBLEM", p)
    if "--check" not in sys.argv:
        with open(os.path.join(HERE, "en.json"), "w", encoding="utf-8") as f:
            json.dump(en, f, ensure_ascii=False, indent=1)
            f.write("\n")
        print(f"en.json: {len(en)} strings")
    bad = bool(problems)
    for code in LANGUAGES:
        if code == "en":
            continue
        m = missing(code, en)
        if m:
            bad = True
            print(f"{code}.json: {len(m)} missing")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
