#!/usr/bin/env python3
"""Tkinter GUI for repo2dataset."""

import json, os, threading, subprocess, tempfile
from pathlib import Path
from tkinter import ttk, messagebox, scrolledtext
import tkinter as tk
from urllib.request import Request, urlopen
from urllib.error import HTTPError

GITHUB_API = "https://api.github.com"
CONFIG = {"token": os.environ.get("GITHUB_TOKEN", "")}

def gh_api(path):
    url = f"{GITHUB_API}{path}"
    headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "repo2dataset"}
    if CONFIG["token"]:
        headers["Authorization"] = f"token {CONFIG['token']}"
    try:
        with urlopen(Request(url, headers=headers), timeout=15) as r:
            return json.loads(r.read())
    except HTTPError as e:
        return {"error": f"HTTP {e.code}"}
    except Exception as e:
        return {"error": str(e)}


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("repo2dataset")
        self.geometry("800x700")

        self.repos = []
        self.selected_repos = []
        self.output_dir = Path("./dataset")

        self._build_widgets()

    def _build_widgets(self):
        # -- Input frame --
        f = ttk.LabelFrame(self, text="Search Criteria", padding=10)
        f.pack(fill="x", padx=10, pady=5)

        row = 0
        ttk.Label(f, text="Language:").grid(row=row, column=0, sticky="w")
        self.lang = ttk.Entry(f, width=20)
        self.lang.insert(0, "Python")
        self.lang.grid(row=row, column=1, sticky="ew", padx=5)

        ttk.Label(f, text="Domain:").grid(row=row, column=2, sticky="w", padx=(10,0))
        self.domain = ttk.Entry(f, width=20)
        self.domain.grid(row=row, column=3, sticky="ew", padx=5)
        row += 1

        ttk.Label(f, text="Min Stars:").grid(row=row, column=0, sticky="w")
        self.min_stars = ttk.Entry(f, width=20)
        self.min_stars.insert(0, "1000")
        self.min_stars.grid(row=row, column=1, sticky="ew", padx=5)

        ttk.Label(f, text="Max Repos:").grid(row=row, column=2, sticky="w", padx=(10,0))
        self.max_repos = ttk.Entry(f, width=20)
        self.max_repos.insert(0, "5")
        self.max_repos.grid(row=row, column=3, sticky="ew", padx=5)
        row += 1

        ttk.Label(f, text="Output Dir:").grid(row=row, column=0, sticky="w")
        self.outdir = ttk.Entry(f, width=50)
        self.outdir.insert(0, "./dataset")
        self.outdir.grid(row=row, column=1, columnspan=3, sticky="ew", padx=5)
        row += 1

        # Checkboxes
        cbf = ttk.Frame(f)
        cbf.grid(row=row, column=0, columnspan=4, sticky="w", pady=5)
        self.include_tests = tk.BooleanVar(value=False)
        ttk.Checkbutton(cbf, text="Include test files", variable=self.include_tests).pack(side="left", padx=5)
        self.include_code = tk.BooleanVar(value=True)
        ttk.Checkbutton(cbf, text="Full source code", variable=self.include_code).pack(side="left", padx=5)
        self.include_prs = tk.BooleanVar(value=True)
        ttk.Checkbutton(cbf, text="Bug-fix PRs", variable=self.include_prs).pack(side="left", padx=5)
        self.include_commits = tk.BooleanVar(value=True)
        ttk.Checkbutton(cbf, text="Fix commits", variable=self.include_commits).pack(side="left", padx=5)

        # Buttons
        bf = ttk.Frame(self)
        bf.pack(fill="x", padx=10, pady=5)
        ttk.Button(bf, text="Search Repos", command=self._search).pack(side="left", padx=2)
        self.extract_btn = ttk.Button(bf, text="Build Dataset", command=self._extract, state="disabled")
        self.extract_btn.pack(side="left", padx=2)
        ttk.Button(bf, text="View Manifest", command=self._view_manifest).pack(side="left", padx=2)
        ttk.Button(bf, text="Open Output", command=self._open_output).pack(side="left", padx=2)

        # Repo list
        ttk.Label(self, text="Repos (click to select/deselect):").pack(anchor="w", padx=10)
        rlf = ttk.Frame(self)
        rlf.pack(fill="both", expand=True, padx=10, pady=2)
        scroll = ttk.Scrollbar(rlf)
        scroll.pack(side="right", fill="y")
        self.repo_list = tk.Listbox(rlf, selectmode="multiple", yscrollcommand=scroll.set)
        self.repo_list.pack(side="left", fill="both", expand=True)
        scroll.config(command=self.repo_list.yview)

        # Status / log
        ttk.Label(self, text="Log:").pack(anchor="w", padx=10)
        self.log = scrolledtext.ScrolledText(self, height=10, state="disabled")
        self.log.pack(fill="both", padx=10, pady=5)

        # Progress
        self.progress = ttk.Progressbar(self, mode="indeterminate")
        self.progress.pack(fill="x", padx=10, pady=2)

    def _log(self, msg):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")
        self.update_idletasks()

    def _search(self):
        self.repo_list.delete(0, "end")
        self.repos = []
        self.extract_btn.configure(state="disabled")

        lang = self.lang.get().strip() or "Python"
        domain = self.domain.get().strip()
        stars = self.min_stars.get().strip() or "1000"
        limit = self.max_repos.get().strip() or "5"

        def run():
            self.progress.start()
            self._log(f"Searching {lang} repos (stars>{stars}, domain={domain or 'any'})...")
            q = f"language:{lang} stars:>{stars}"
            if domain:
                q += f" topic:{domain}"
            data = gh_api(f"/search/repositories?q={q}&sort=stars&per_page={limit}")
            if data.get("error"):
                self._log(f"Error: {data['error']}")
                self.progress.stop()
                return
            items = data.get("items", [])
            if not items:
                self._log("No repos found. Try broader criteria.")
                self.progress.stop()
                return
            for r in items:
                label = f"{r['full_name']}  ({r['stargazers_count']}★)  {r.get('description') or ''}"
                self.repos.append(r)
                self.repo_list.insert("end", label)
            self._log(f"Found {len(items)} repos. Select ones to use, then click Build Dataset.")
            self.extract_btn.configure(state="normal")
            self.progress.stop()

        threading.Thread(target=run, daemon=True).start()

    def _extract(self):
        sel = self.repo_list.curselection()
        if not sel:
            messagebox.showinfo("Select Repos", "Select repos from the list first.")
            return

        self.output_dir = Path(self.outdir.get().strip() or "./dataset")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        outfile = self.output_dir / "dataset.jsonl"

        repos = [self.repos[i] for i in sel]

        def run():
            self.progress.start()
            self.extract_btn.configure(state="disabled")
            counts = {"code": 0, "pr": 0, "commit": 0}
            clone_dir = self.output_dir / "clones"
            token = CONFIG["token"] or None

            for r in repos:
                repo = r["full_name"]
                self._log(f"\n=== {repo} ===")

                if self.include_code.get():
                    repo_dir = Path(clone_dir) / repo.replace("/", "_")
                    if not repo_dir.exists():
                        self._log(f"  Cloning...")
                        subprocess.run(["git", "clone", "--depth", "1", f"https://github.com/{repo}.git", str(repo_dir)],
                                       capture_output=True, timeout=120)
                    exts = {".py", ".rs", ".ts", ".tsx", ".js", ".jsx", ".go", ".java", ".rb", ".c", ".cpp", ".kt"}
                    skip = {"node_modules", "vendor", ".git", "target", "build", "dist", "__pycache__"}
                    n = 0
                    for fpath in Path(repo_dir).rglob("*"):
                        if fpath.suffix not in exts:
                            continue
                        if any(p in skip for p in fpath.parts):
                            continue
                        if not self.include_tests.get() and ("test" in fpath.stem.lower() or "spec" in fpath.stem.lower()):
                            continue
                        try:
                            if fpath.stat().st_size > 50000:
                                continue
                            content = fpath.read_text("utf-8", errors="replace")
                        except Exception:
                            continue
                        rel = fpath.relative_to(repo_dir)
                        with open(outfile, "a") as f:
                            f.write(json.dumps({"instruction": f"Write the file `{rel}` from {repo}",
                                                "response": content, "repo": repo, "source": "code", "path": str(rel)},
                                               ensure_ascii=False) + "\n")
                        n += 1
                    counts["code"] += n
                    self._log(f"  → {n} code examples")

                if self.include_prs.get():
                    q = f"repo:{repo} type:pr state:merged"
                    data = gh_api(f"/search/issues?q={q}&sort=created&per_page=50")
                    if data and "items" in data:
                        n = 0
                        for item in data["items"]:
                            title = item["title"]
                            if not re.search(r"(?i)(fix|bug|hotfix|patch|resolve|error|crash)", title):
                                continue
                            pr_data = gh_api(f"/repos/{repo}/pulls/{item['number']}")
                            if not pr_data or not pr_data.get("merged"):
                                continue
                            diff_url = pr_data.get("diff_url")
                            patch = ""
                            if diff_url:
                                try:
                                    req = Request(diff_url, headers={"User-Agent": "repo2dataset"})
                                    with urlopen(req, timeout=10) as r:
                                        patch = r.read().decode()
                                except Exception:
                                    pass
                            body = pr_data.get("body") or ""
                            with open(outfile, "a") as f:
                                f.write(json.dumps({"instruction": f"{title}\n\n{body}",
                                                    "response": patch, "repo": repo, "source": "pr", "ref": f"#{item['number']}"},
                                                   ensure_ascii=False) + "\n")
                            n += 1
                        counts["pr"] += n
                        self._log(f"  → {n} bug-fix PRs")

                if self.include_commits.get():
                    data = gh_api(f"/repos/{repo}/commits?per_page=100")
                    if data and isinstance(data, list):
                        n = 0
                        for commit in data:
                            msg = commit["commit"]["message"]
                            if not re.search(r"(?i)(fix|bug|resolve|close|hotfix|patch)", msg.split("\n")[0]):
                                continue
                            sha = commit["sha"]
                            detail = gh_api(f"/repos/{repo}/commits/{sha}")
                            if not detail:
                                continue
                            patch = ""
                            for f in detail.get("files", []):
                                p = f.get("patch")
                                if p:
                                    patch += f"--- {f['filename']}\n{p}\n"
                            with open(outfile, "a") as f:
                                f.write(json.dumps({"instruction": msg, "response": patch,
                                                    "repo": repo, "source": "commit", "ref": sha[:8]},
                                                   ensure_ascii=False) + "\n")
                            n += 1
                        counts["commit"] += n
                        self._log(f"  → {n} fix commits")

            # Manifest
            total = sum(counts.values())
            manifest = {"total": total, "by_source": counts, "repos": [r["full_name"] for r in repos]}
            with open(self.output_dir / "manifest.json", "w") as f:
                json.dump(manifest, f, indent=2)

            self._log(f"\n{'='*40}")
            self._log(f"Done. {total} examples → {outfile}")
            for k, v in counts.items():
                if v:
                    self._log(f"  {k}: {v}")
            self.extract_btn.configure(state="normal")
            self.progress.stop()

        threading.Thread(target=run, daemon=True).start()

    def _view_manifest(self):
        mf = self.output_dir / "manifest.json"
        if mf.exists():
            text = mf.read_text()
            messagebox.showinfo("manifest.json", text)
        else:
            messagebox.showinfo("No Manifest", "Build a dataset first.")

    def _open_output(self):
        import subprocess, sys
        d = str(self.output_dir.resolve())
        if sys.platform == "darwin":
            subprocess.run(["open", d])
        elif sys.platform == "win32":
            subprocess.run(["explorer", d])
        else:
            subprocess.run(["xdg-open", d], stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    import re
    App().mainloop()
