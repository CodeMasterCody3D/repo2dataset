#!/usr/bin/env python3
"""Turn GitHub repos into AI training datasets.

Extracts full source code + bug-fix PRs + fix commits + closed issues,
assembles them into structured (problem→solution→diff) JSONL examples.

Repo discovery sources:
  - github : GitHub API search (default, needs GITHUB_TOKEN for high limits)
  - hf     : 40M repo metadata dataset on HuggingFace (pip install datasets)

Usage:
  python src/repo2dataset.py search --source hf --language python --limit 5
  python src/repo2dataset.py extract --repo gruns/icecream --output ./dataset
  python src/repo2dataset.py all --source hf --language python --domain cli --output ./dataset
"""

import argparse, json, os, re, subprocess, sys
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

GITHUB_API = "https://api.github.com"

def eprint(*a, **kw):
    print(*a, file=sys.stderr, **kw)

def gh_api(path, token=None):
    url = f"{GITHUB_API}{path}"
    headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "repo2dataset"}
    if token:
        headers["Authorization"] = f"token {token}"
    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except HTTPError as e:
        eprint(f"API error {e.code} for {url}: {e.read().decode()}")
        return None

def search_repos(language, domain=None, min_stars=1000, limit=10, source="github", token=None):
    if source == "hf":
        return search_repos_hf(language, domain, min_stars, limit)
    q = f"language:{language} stars:>{min_stars}"
    if domain:
        q += f" topic:{domain}"
    data = gh_api(f"/search/repositories?q={q}&sort=stars&per_page={limit}", token)
    if not data:
        return []
    return [{
        "full_name": r["full_name"],
        "stars": r["stargazers_count"],
        "description": r["description"],
        "language": r["language"],
        "url": r["html_url"],
        "open_issues": r["open_issues_count"],
        "pushed_at": r["pushed_at"],
    } for r in data.get("items", [])]

def search_repos_hf(language, domain=None, min_stars=1000, limit=10):
    """Search repos using the HuggingFace 40M metadata dataset."""
    try:
        from datasets import load_dataset
    except ImportError:
        eprint("HuggingFace datasets library not installed. Run: pip install datasets")
        return []

    eprint("Loading repo metadata from HuggingFace (40M dataset)...")
    ds = load_dataset("ibragim-bad/github-repos-metadata-40M", split="sample", streaming=True)

    results = []
    for row in ds:
        lang = (row.get("language") or "").lower()
        if lang != language.lower():
            continue
        stars = row.get("watchers_count") or 0
        if stars < min_stars:
            continue
        name = row.get("repo_name") or ""
        if not name or "/" not in name:
            continue
        results.append({
            "full_name": name,
            "stars": stars,
            "description": row.get("description") or "",
            "language": row.get("language") or language,
            "url": f"https://github.com/{name}",
        })
        if len(results) >= limit:
            break

    results.sort(key=lambda r: r["stars"], reverse=True)
    eprint(f"  Found {len(results)} repos matching {language} >= {min_stars}★")
    return results

def extract_code(repo, clone_dir, output_file, include_tests=True, max_files=200, token=None):
    """Clone repo and write each source file as a training example."""
    repo_dir = Path(clone_dir) / repo.replace("/", "_")
    if not repo_dir.exists():
        eprint(f"Cloning {repo}...")
        subprocess.run(
            ["git", "clone", "--depth", "1", f"https://github.com/{repo}.git", str(repo_dir)],
            capture_output=True, timeout=120
        )

    exts = {".py", ".rs", ".ts", ".tsx", ".js", ".jsx", ".go", ".java", ".rb",
            ".c", ".cpp", ".h", ".hpp", ".kt", ".swift", ".scala", ".zig", ".rl"}
    skip_dirs = {"node_modules", "vendor", ".git", "target", "build", "dist",
                 "__pycache__", ".venv", "venv", "env", ".tox", "egg-info", "site-packages"}

    examples = []
    for fpath in repo_dir.rglob("*"):
        if fpath.suffix not in exts:
            continue
        if any(p in skip_dirs for p in fpath.parts):
            continue
        if not include_tests and ("test" in fpath.stem.lower() or "spec" in fpath.stem.lower()):
            continue

        try:
            size = fpath.stat().st_size
            if size > 50_000:  # skip files > 50 KB
                continue
            content = fpath.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        rel = fpath.relative_to(repo_dir)
        examples.append({
            "instruction": f"Write the file `{rel}` from {repo}",
            "response": content,
            "repo": repo,
            "source": "code",
            "path": str(rel),
        })

        if len(examples) >= max_files:
            break

    with open(output_file, "a") as f:
        for ex in examples:
            f.write(json.dumps(ex, ensure_ascii=False) + "\n")

    eprint(f"  → {len(examples)} code examples")
    return len(examples)

def extract_bugfix_prs(repo, output_file, limit=50, token=None):
    """Extract merged PRs with fix-related labels/titles."""
    q = f"repo:{repo} type:pr state:merged"
    data = gh_api(f"/search/issues?q={q}&sort=created&per_page={limit}", token)
    if not data:
        return 0

    fix_keywords = re.compile(r"(?i)(fix|bug|hotfix|patch|resolve|error|crash)")
    examples = []

    for item in data.get("items", []):
        title = item["title"]
        if not fix_keywords.search(title):
            labels = [l["name"] for l in item.get("labels", [])]
            if not any("bug" in l.lower() or "fix" in l.lower() for l in labels):
                continue

        pr_data = gh_api(f"/repos/{repo}/pulls/{item['number']}", token)
        if not pr_data or not pr_data.get("merged"):
            continue

        body = (pr_data.get("body") or "").strip()
        diff_url = pr_data.get("diff_url")
        patch = ""
        if diff_url:
            try:
                req = Request(diff_url, headers={"User-Agent": "repo2dataset"})
                if token:
                    req.add_header("Authorization", f"token {token}")
                with urlopen(req, timeout=15) as r:
                    patch = r.read().decode()
            except Exception:
                pass

        instruction = title
        if body:
            instruction += "\n\n" + body

        examples.append({
            "instruction": instruction,
            "response": patch or "(see PR for diff)",
            "repo": repo,
            "source": "pr",
            "ref": f"#{item['number']}",
        })

    with open(output_file, "a") as f:
        for ex in examples:
            f.write(json.dumps(ex, ensure_ascii=False) + "\n")

    eprint(f"  → {len(examples)} bug-fix PR examples")
    return len(examples)

def extract_bugfix_commits(repo, output_file, limit=100, token=None):
    """Extract commits with fix-related messages."""
    data = gh_api(f"/repos/{repo}/commits?per_page={limit}", token)
    if not data:
        return 0

    fix_re = re.compile(r"(?i)(fix|bug|resolve|close|hotfix|patch|error|crash)")
    examples = []

    for commit in data:
        msg = commit["commit"]["message"]
        if not fix_re.search(msg.split("\n")[0]):
            continue

        sha = commit["sha"]
        detail = gh_api(f"/repos/{repo}/commits/{sha}", token)
        if not detail:
            continue

        patch = ""
        files_data = detail.get("files", [])
        for f in files_data:
            p = f.get("patch")
            if p:
                patch += f"--- {f['filename']}\n+++ {f['filename']}\n{p}\n"

        examples.append({
            "instruction": msg,
            "response": patch or "(see commit for diff)",
            "repo": repo,
            "source": "commit",
            "ref": sha[:8],
        })

    with open(output_file, "a") as f:
        for ex in examples:
            f.write(json.dumps(ex, ensure_ascii=False) + "\n")

    eprint(f"  → {len(examples)} bug-fix commit examples")
    return len(examples)

def extract_issues(repo, output_file, limit=100, token=None):
    """Extract closed bug issues."""
    q = f"repo:{repo} type:issue state:closed label:bug"
    data = gh_api(f"/search/issues?q={q}&sort=updated&per_page={limit}", token)
    if not data:
        return 0

    examples = []
    for item in data.get("items", []):
        body = (item.get("body") or "").strip()
        examples.append({
            "instruction": item["title"],
            "response": body or "(no description)",
            "repo": repo,
            "source": "issue",
            "ref": f"#{item['number']}",
        })

    with open(output_file, "a") as f:
        for ex in examples:
            f.write(json.dumps(ex, ensure_ascii=False) + "\n")

    eprint(f"  → {len(examples)} issue examples")
    return len(examples)

def build_manifest(output_dir, repos, counts):
    manifest = {
        "total": sum(counts.values()),
        "by_source": dict(counts),
        "repos": list(repos),
        "generated_by": "repo2dataset",
    }
    with open(Path(output_dir) / "manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)

def cmd_search(args):
    repos = search_repos(args.language, args.domain, args.min_stars, args.limit, args.source)
    if not repos:
        eprint("No repos found.")
        return
    print(json.dumps(repos, indent=2))

def cmd_extract(args):
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / "dataset.jsonl"

    token = os.environ.get("GITHUB_TOKEN")
    clone_dir = output_dir / "clones"

    repo = args.repo
    eprint(f"\nProcessing {repo}...")
    counts = {}

    n = extract_code(repo, clone_dir, output_file, args.include_tests, args.max_files, token)
    counts["code"] = n

    if not args.code_only:
        n = extract_bugfix_prs(repo, output_file, args.pr_limit, token)
        if n:
            counts["pr"] = n
        n = extract_bugfix_commits(repo, output_file, args.commit_limit, token)
        if n:
            counts["commit"] = n

    build_manifest(output_dir, {repo}, counts)
    eprint(f"\nDone. Dataset: {output_file}")
    eprint(f"Manifest: {output_dir / 'manifest.json'}")

def cmd_all(args):
    repos = search_repos(args.language, args.domain, args.min_stars, args.limit, args.source)
    if not repos:
        eprint("No repos found with those criteria.")
        return
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / "dataset.jsonl"

    token = os.environ.get("GITHUB_TOKEN")
    clone_dir = output_dir / "clones"
    all_repos = set()
    total_counts = {}

    for r in repos:
        repo = r["full_name"]
        all_repos.add(repo)
        eprint(f"\n=== {repo} ({r['stars']} ★) ===")
        n = extract_code(repo, clone_dir, output_file, args.include_tests, args.max_files, token)
        total_counts["code"] = total_counts.get("code", 0) + n

        if not args.code_only:
            n = extract_bugfix_prs(repo, output_file, args.pr_limit, token)
            if n:
                total_counts["pr"] = total_counts.get("pr", 0) + n
            n = extract_bugfix_commits(repo, output_file, args.commit_limit, token)
            if n:
                total_counts["commit"] = total_counts.get("commit", 0) + n

    build_manifest(output_dir, all_repos, total_counts)
    eprint(f"\n{'='*40}")
    eprint(f"Done. {total_counts.get('code', 0)} code + {total_counts.get('pr', 0)} PR "
           f"+ {total_counts.get('commit', 0)} commit examples")
    eprint(f"Dataset: {output_file}")

def cmd_tui(args):
    """Simple interactive TUI."""
    print("\n=== repo2dataset Interactive ===\n")
    lang = input("Programming language [Python]: ").strip() or "Python"
    domain = input("Domain (web/cli/data-science/etc) []: ").strip() or ""
    stars = input("Min stars [1000]: ").strip() or "1000"
    count = input("Number of repos [3]: ").strip() or "3"
    include_tests = input("Include test files? (y/N): ").strip().lower() == "y"
    do_code = input("Include full source code? (Y/n): ").strip().lower() != "n"
    output = input("Output directory [./dataset]: ").strip() or "./dataset"

    src = input("Repo source [github/hf]: ").strip().lower() or "github"
    token = os.environ.get("GITHUB_TOKEN")
    eprint(f"\nSearching for {lang} repos in '{domain or 'any'} domain'...")
    repos = search_repos(lang, domain, int(stars), int(count), src, token)
    if not repos:
        eprint("Nothing found. Try broader criteria.")
        return

    print(f"\nTop repos:")
    for i, r in enumerate(repos, 1):
        print(f"  {i}. {r['full_name']} ({r['stars']}★) — {r['description'] or 'no description'}")

    picks = input(f"\nPick repos to process (e.g. 1,2,3 or Enter for all): ").strip()
    if picks:
        indices = [int(x.strip()) for x in picks.split(",") if x.strip().isdigit()]
        repos = [repos[i-1] for i in indices if 1 <= i <= len(repos)]

    output_dir = Path(output)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / "dataset.jsonl"
    clone_dir = output_dir / "clones"
    all_repos = set()
    counts = {}

    for r in repos:
        repo = r["full_name"]
        all_repos.add(repo)
        eprint(f"\n=== {repo} ===")
        if do_code:
            n = extract_code(repo, clone_dir, output_file, include_tests, token=token)
            counts["code"] = counts.get("code", 0) + n
        n = extract_bugfix_prs(repo, output_file, token=token)
        if n:
            counts["pr"] = counts.get("pr", 0) + n
        n = extract_bugfix_commits(repo, output_file, token=token)
        if n:
            counts["commit"] = counts.get("commit", 0) + n

    build_manifest(output_dir, all_repos, counts)
    total = sum(counts.values())
    eprint(f"\n{'='*40}")
    eprint(f"Dataset built: {total} examples → {output_file}")
    for k, v in counts.items():
        eprint(f"  {k}: {v}")

def main():
    p = argparse.ArgumentParser(description="repo2dataset — GitHub repos → AI training data")
    p.add_argument("--token", help="GitHub token (or GITHUB_TOKEN env var)")

    sub = p.add_subparsers(dest="cmd")

    # search
    sp = sub.add_parser("search", help="Search for repos matching criteria")
    sp.add_argument("--source", choices=["github", "hf"], default="github", help="Repo source (github API or HuggingFace dataset)")
    sp.add_argument("--language", "-l", default="python", help="Programming language")
    sp.add_argument("--domain", "-d", help="Topic/domain filter")
    sp.add_argument("--min-stars", type=int, default=1000)
    sp.add_argument("--limit", type=int, default=10)

    # extract
    ep = sub.add_parser("extract", help="Extract dataset from a single repo")
    ep.add_argument("--repo", "-r", required=True, help="owner/repo")
    ep.add_argument("--output", "-o", default="./dataset", help="Output directory")
    ep.add_argument("--include-tests", action="store_true", help="Include test files")
    ep.add_argument("--max-files", type=int, default=200)
    ep.add_argument("--pr-limit", type=int, default=50)
    ep.add_argument("--commit-limit", type=int, default=100)
    ep.add_argument("--code-only", action="store_true", help="Only extract code, skip PRs")

    # all
    ap = sub.add_parser("all", help="Search + extract dataset from multiple repos")
    ap.add_argument("--source", choices=["github", "hf"], default="github", help="Repo source")
    ap.add_argument("--language", "-l", default="python")
    ap.add_argument("--domain", "-d", help="Topic filter")
    ap.add_argument("--min-stars", type=int, default=1000)
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--output", "-o", default="./dataset")
    ap.add_argument("--include-tests", action="store_true")
    ap.add_argument("--max-files", type=int, default=200)
    ap.add_argument("--pr-limit", type=int, default=50)
    ap.add_argument("--commit-limit", type=int, default=100)
    ap.add_argument("--code-only", action="store_true")

    # tui
    sub.add_parser("tui", help="Interactive mode (asks questions)")

    args = p.parse_args()
    if not args.cmd:
        p.print_help()
        return

    os.environ.setdefault("GITHUB_TOKEN", args.token or "")
    os.environ["GIT_PYTHON_GIT_EXECUTABLE"] = "git"  # ensure git is found

    if args.cmd == "search":
        cmd_search(args)
    elif args.cmd == "extract":
        cmd_extract(args)
    elif args.cmd == "all":
        cmd_all(args)
    elif args.cmd == "tui":
        cmd_tui(args)

if __name__ == "__main__":
    main()
