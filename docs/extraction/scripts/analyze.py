import ast, os, sys, json, collections, re

REPO = "/Users/Harland/Go/penpal"
PKG = os.path.join(REPO, "penpal")
SP = "/private/tmp/claude-502/-Users-Harland-Go-penpal/c7664793-02ba-4616-81c2-5db7c60940bf/scratchpad"
STDLIB = set(sys.stdlib_module_names)
SUBPKGS = {d for d in os.listdir(os.path.join(REPO,"penpal")) if os.path.isdir(os.path.join(REPO,"penpal",d)) and d != "__pycache__"}
PYOBJC = {"AppKit","Foundation","Quartz","objc","Vision","CoreServices","ApplicationServices","Cocoa",
          "CoreFoundation","LaunchServices","Security","ScreenCaptureKit","CoreLocation","AVFoundation",
          "PyObjCTools","Speech","NaturalLanguage","CoreML","Contacts","EventKit","SystemConfiguration",
          "CoreWLAN","IOKit","Metal","MediaPlayer","Network","libdispatch","WebKit","ImageIO"}

def rel(p): return os.path.relpath(p, REPO)

def unit_of_parts(parts):
    # parts like ['penpal','capture','x']  -> unit name
    if len(parts) < 2: return "(root:__init__)"
    p = parts[1]
    if len(parts) == 2 and p.endswith(".py") is False:
        # could be subpackage or module name without .py; treat as module
        return "root:" + p
    if len(parts) == 2:
        return "root:" + p[:-3] if p.endswith(".py") else p
    return p

pyfiles = []
for dp, dn, fn in os.walk(PKG):
    dn[:] = [d for d in dn if d != "__pycache__"]
    for f in fn:
        if f.endswith(".py"):
            pyfiles.append(os.path.join(dp, f))
pyfiles.sort()

info = {}
for path in pyfiles:
    r = rel(path)
    src = open(path, encoding="utf-8").read()
    loc = src.count("\n") + (0 if src.endswith("\n") or not src else 1)
    try:
        tree = ast.parse(src, filename=path)
    except SyntaxError as e:
        tree = None
    doc = None
    if tree is not None:
        d = ast.get_docstring(tree)
        if d:
            for line in d.splitlines():
                if line.strip():
                    doc = line.strip(); break
    modparts = r[:-3].split("/")  # penpal/x/y or penpal/x/__init__
    if modparts[-1] == "__init__":
        pkgparts = modparts[:-1]
    else:
        pkgparts = modparts[:-1]
    info[r] = dict(loc=loc, doc=doc, path=path, modparts=modparts, pkgparts=pkgparts, tree=tree)

# unit of each file
def file_unit(r):
    parts = r.split("/")
    if len(parts) == 2:  # penpal/x.py  (root)
        return "root:" + parts[1][:-3] if parts[1] != "__init__.py" else "root:__init__"
    return parts[1]

edges = collections.Counter()      # (src_unit, dst_unit) all imports
top_edges = collections.Counter()  # module-level only
file_edges = collections.defaultdict(set)
third = collections.defaultdict(collections.Counter)
pyobjc_files = []
third_by_file = {}

def resolve(pkgparts, level, module, names):
    """return list of dotted target part-lists"""
    out = []
    if level:
        base = pkgparts[: len(pkgparts) - (level - 1)] if level - 1 <= len(pkgparts) else []
        if module:
            out.append(base + module.split("."))
        else:
            out.extend(base + [n] for n in names)
    else:
        out.append(module.split("."))
    return out

def target_unit(parts):
    if not parts or parts[0] != "penpal":
        return None
    if len(parts) == 1:
        return "root:__init__"
    p = parts[1]
    if len(parts) == 2:
        if p in SUBPKGS: return p
        return "root:" + p
    return p

def target_file(parts):
    # map a dotted target to repo file, if it is a known file (for file-level cycle analysis)
    cand = "/".join(parts)
    for c in (cand + ".py", cand + "/__init__.py"):
        if os.path.join(REPO, c) in [v["path"] for v in info.values()] or c in info:
            return c
    return None

for r, v in info.items():
    if v["tree"] is None:
        continue
    su = file_unit(r)
    top_level_nodes = set(id(n) for n in v["tree"].body)
    for node in ast.walk(v["tree"]):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            is_top = id(node) in top_level_nodes
            if isinstance(node, ast.Import):
                targets = [a.name.split(".") for a in node.names]
                lvl = 0
            else:
                lvl = node.level or 0
                names = [a.name for a in node.names]
                targets = resolve(v["pkgparts"], lvl, node.module, names)
                if lvl == 0:
                    targets = [t for t in targets]
            for t in targets:
                if lvl == 0 and isinstance(node, ast.ImportFrom) is False:
                    pass
                if t and t[0] == "penpal":
                    du = target_unit(t)
                    # for from-penpal-import-name where name is a module, handled via names above for relative only
                    if du and du != su:
                        edges[(su, du)] += 1
                        if is_top: top_edges[(su, du)] += 1
                        tf = target_file(t) or target_file(t[:2]) or ("/".join(t) + "(?)")
                        file_edges[r].add((tf, node.lineno))
                elif t and t[0] not in STDLIB and not (lvl and lvl > 0):
                    top = t[0]
                    third[su][top] += 1
                    if top in PYOBJC:
                        pyobjc_files.append((r, node.lineno, top))
                    third_by_file.setdefault(r, set()).add(top)
                if lvl and t and t[0] != "penpal":
                    pass

# ensure relative resolution: for relative imports, resolve() returns full path starting with 'penpal' only if pkgparts start with penpal
# (pkgparts already start with 'penpal' since modparts is repo-relative)

# cycles (Tarjan) on unit graph
def scc(graph):
    index = {}; low = {}; st = []; on = set(); res = []; cnt = [0]
    nodes = set(graph) | {b for a in graph for b in graph[a]}
    def strong(v):
        index[v] = low[v] = cnt[0]; cnt[0] += 1; st.append(v); on.add(v)
        for w in graph.get(v, ()):
            if w not in index:
                strong(w); low[v] = min(low[v], low[w])
            elif w in on:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = st.pop(); on.discard(w); comp.append(w)
                if w == v: break
            if len(comp) > 1: res.append(sorted(comp))
    for v in nodes:
        if v not in index: strong(v)
    return res

ug = collections.defaultdict(set)
for (a, b) in edges: ug[a].add(b)
cycles_all = scc(ug)
tug = collections.defaultdict(set)
for (a, b) in top_edges: tug[a].add(b)
cycles_top = scc(tug)

# file-level cycles
fg = collections.defaultdict(set)
for r, s in file_edges.items():
    for tf, ln in s:
        if tf in info and not tf.endswith("(?)"):
            fg[r].add(tf)
file_cycles = scc(fg)

out = {
  "info": {r: {k: v[k] for k in ("loc","doc")} for r, v in info.items()},
  "edges": {f"{a} -> {b}": c for (a, b), c in sorted(edges.items())},
  "top_edges": {f"{a} -> {b}": c for (a, b), c in sorted(top_edges.items())},
  "cycles_all": cycles_all, "cycles_top": cycles_top, "file_cycles": file_cycles,
  "third": {u: dict(c) for u, c in third.items()},
  "pyobjc_files": pyobjc_files,
  "third_by_file": {k: sorted(v) for k, v in third_by_file.items()},
  "file_edges": {r: sorted(list(s)) for r, s in file_edges.items()},
}
json.dump(out, open(os.path.join(SP, "analysis.json"), "w"), ensure_ascii=False, indent=1, default=list)
print("files", len(info))
print("cycles_all", cycles_all)
print("cycles_top", cycles_top)
print("file_cycles", file_cycles)
print("pyobjc files:", len({f for f,_,_ in pyobjc_files}), sorted({(f,m) for f,_,m in pyobjc_files})[:60])
