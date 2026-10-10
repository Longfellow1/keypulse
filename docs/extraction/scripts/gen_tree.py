import os, ast, json, collections
REPO = "/Users/Harland/Go/keypulse"
SP = "/private/tmp/claude-502/-Users-Harland-Go-keypulse/c7664793-02ba-4616-81c2-5db7c60940bf/scratchpad"
A = json.load(open(os.path.join(SP, "analysis.json")))
INFO = A["info"]

def wc(path):
    b = open(path, "rb").read()
    return b.count(b"\n") + (0 if (not b or b.endswith(b"\n")) else 1)

def purpose_py(path):
    try:
        t = ast.parse(open(path, encoding="utf-8").read())
    except SyntaxError:
        return "(syntax error)"
    d = ast.get_docstring(t) or ""
    for l in d.splitlines():
        if l.strip():
            return l.strip()[:110]
    return "(no docstring)"

def purpose_other(path):
    ext = path.rsplit(".", 1)[-1]
    if ext == "md":
        for l in open(path, encoding="utf-8").read().splitlines():
            if l.strip():
                return "md: " + l.strip().lstrip("#").strip()[:100]
        return "md: (empty)"
    if ext == "json": return "JSON data"
    if ext == "yaml": return "YAML data (holiday calendar)"
    return ext

# ---------- keypulse/ tree ----------
tree = {"dirs": {}, "files": []}
rows_by_sub = collections.defaultdict(lambda: {"py": 0, "loc": 0, "all": 0})
for dp, dn, fn in os.walk(os.path.join(REPO, "keypulse")):
    dn[:] = sorted(d for d in dn if d != "__pycache__")
    for f in sorted(fn):
        if f == ".DS_Store": continue
        full = os.path.join(dp, f)
        rel = os.path.relpath(full, os.path.join(REPO, "keypulse"))
        parts = rel.split(os.sep)
        node = tree
        for d in parts[:-1]:
            node = node["dirs"].setdefault(d, {"dirs": {}, "files": []})
        if f.endswith(".py"):
            loc = wc(full); pur = purpose_py(full)
            rows_by_sub[parts[0] if len(parts) > 1 else "(root)"]["py"] += 1
            rows_by_sub[parts[0] if len(parts) > 1 else "(root)"]["loc"] += loc
        else:
            loc = wc(full); pur = purpose_other(full)
        node["files"].append((f, loc, pur))

def subtotal(node):
    py = loc = 0
    for f, l, _ in node["files"]:
        if f.endswith(".py"): py += 1; loc += l
    for d in node["dirs"].values():
        p, l = subtotal(d); py += p; loc += l
    return py, loc

def render(node, depth, out):
    ind = "  " * depth
    for f, loc, pur in sorted(node["files"]):
        out.append(f"{ind}{f:<36}{loc:>6}  {pur}")
    for d in sorted(node["dirs"]):
        sub = node["dirs"][d]
        p, l = subtotal(sub)
        out.append(f"{ind}{d}/  [{p} .py, {l:,} LOC]")
        render(sub, depth + 1, out)

tree_lines = []
tree_lines.append(f"keypulse/  [{subtotal(tree)[0]} .py, {subtotal(tree)[1]:,} LOC]")
render(tree, 1, tree_lines)

# ---------- tests grouping ----------
UNITS_PKG = {d for d in os.listdir(os.path.join(REPO, "keypulse")) if os.path.isdir(os.path.join(REPO, "keypulse", d)) and d != "__pycache__"}
def unit_of(parts):
    if len(parts) < 2: return None
    p = parts[1]
    if len(parts) == 2:
        return p if p in UNITS_PKG else "root:" + p.replace(".py", "")
    return p
groups = collections.defaultdict(lambda: [0, 0, []])
test_files = sorted(f for f in os.listdir(os.path.join(REPO, "tests")) if f.endswith(".py"))
for f in test_files:
    full = os.path.join(REPO, "tests", f)
    src = open(full, encoding="utf-8").read()
    loc = wc(full)
    try:
        t = ast.parse(src)
    except SyntaxError:
        t = None
    cnt = collections.Counter()
    if t:
        for n in ast.walk(t):
            if isinstance(n, ast.Import):
                for a in n.names:
                    if a.name.startswith("keypulse."):
                        u = unit_of(a.name.split("."))
                        if u: cnt[u] += 1
            elif isinstance(n, ast.ImportFrom) and n.module and n.module.startswith("keypulse") and not n.level:
                u = unit_of(n.module.split("."))
                if u: cnt[u] += 1
    g = cnt.most_common(1)[0][0] if cnt else "(no keypulse import)"
    groups[g][0] += 1; groups[g][1] += loc; groups[g][2].append(f)
test_py_total = sum(v[0] for v in groups.values()); test_loc_total = sum(v[1] for v in groups.values())

fixtures = []
for dp, dn, fn in os.walk(os.path.join(REPO, "tests", "fixtures")):
    dn[:] = [d for d in dn if d != "__pycache__"]
    for f in sorted(fn):
        full = os.path.join(dp, f); fixtures.append((os.path.relpath(full, REPO), wc(full)))
golden = []
for dp, dn, fn in os.walk(os.path.join(REPO, "tests", "golden")):
    dn[:] = [d for d in dn if d != "__pycache__"]
    for f in sorted(fn):
        full = os.path.join(dp, f); golden.append((os.path.relpath(full, REPO), wc(full)))

# ---------- scripts / docs / brain / integrations ----------
scripts = []
for f in sorted(os.listdir(os.path.join(REPO, "scripts"))):
    full = os.path.join(REPO, "scripts", f)
    if not os.path.isfile(full): continue
    if f.endswith(".py"):
        pur = purpose_py(full)
    elif f.endswith(".sh"):
        pur = next((l.strip().lstrip("#").strip() for l in open(full, encoding="utf-8") if l.startswith("#") and l.strip("# \n")), "(no comment)")
    else:
        pur = "launchd plist (scheduled daily/weekly job)"
    scripts.append((f, wc(full), pur))
scripts_dirs = [d for d in os.listdir(os.path.join(REPO, "scripts")) if os.path.isdir(os.path.join(REPO, "scripts", d))]

def count_files(path):
    n = 0
    for dp, dn, fn in os.walk(path):
        dn[:] = [d for d in dn if d != "__pycache__"]
        n += len([f for f in fn if f != ".DS_Store"])
    return n
docs_sub = []
for d in sorted(os.listdir(os.path.join(REPO, "docs"))):
    full = os.path.join(REPO, "docs", d)
    if os.path.isdir(full):
        docs_sub.append((d, count_files(full)))
docs_top = len([f for f in os.listdir(os.path.join(REPO, "docs")) if os.path.isfile(os.path.join(REPO, "docs", f)) and f != ".DS_Store"])

json.dump({"tree_lines": tree_lines, "rows_by_sub": rows_by_sub, "groups": {k: [v[0], v[1], v[2]] for k, v in groups.items()},
           "test_py_total": test_py_total, "test_loc_total": test_loc_total, "fixtures": fixtures, "golden": golden,
           "scripts": scripts, "docs_sub": docs_sub, "docs_top": docs_top,
           "scripts_dirs": scripts_dirs}, open(os.path.join(SP, "tree-data.json"), "w"), ensure_ascii=False, indent=1)
print("tests py", test_py_total, test_loc_total)
for k, v in sorted(groups.items(), key=lambda x: -x[1][0]): print(k, v[0], v[1])
print("fixtures", fixtures); print("golden", golden)
print("docs", docs_sub, docs_top)
print("scripts", scripts)
print("\n".join(tree_lines[:12]))
