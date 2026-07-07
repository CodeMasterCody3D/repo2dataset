#!/usr/bin/env python3
"""Tkinter GUI for repo2dataset."""

import json, os, threading, subprocess
from pathlib import Path
from tkinter import ttk, messagebox, scrolledtext
import tkinter as tk
from urllib.request import Request, urlopen
from urllib.error import HTTPError

GITHUB_API = "https://api.github.com"
HF_API = "https://huggingface.co/api"
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
        self.geometry("900x750")

        self.repos = []
        self.selected_repos = []
        self.output_dir = Path("./dataset")

        # Notebook with tabs
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True)

        self.build_tab = ttk.Frame(self.nb)
        self.hf_tab = ttk.Frame(self.nb)
        self.nb.add(self.build_tab, text="Build Dataset")
        self.nb.add(self.hf_tab, text="HF Datasets")

        self._build_dataset_tab()
        self._build_hf_tab()

    def _build_hf_tab(self):
        f = ttk.Frame(self.hf_tab, padding=10)
        f.pack(fill="x")

        row = 0
        ttk.Label(f, text="Search:").grid(row=row, column=0, sticky="w")
        self.hf_query = ttk.Entry(f, width=50)
        self.hf_query.grid(row=row, column=1, sticky="ew", padx=5)

        ttk.Label(f, text="Task:").grid(row=row, column=2, sticky="w", padx=(10,0))
        self.hf_task = ttk.Combobox(f, values=["", "text-classification", "text-generation",
            "image-classification", "object-detection", "summarization", "translation"],
            state="readonly", width=22)
        self.hf_task.set("")
        self.hf_task.grid(row=row, column=3, sticky="ew", padx=5)
        row += 1

        bf = ttk.Frame(f)
        bf.grid(row=row, column=0, columnspan=4, pady=5)
        ttk.Button(bf, text="Search Datasets", command=self._hf_search).pack(side="left", padx=2)
        self.hf_dl_btn = ttk.Button(bf, text="Download Selected", command=self._hf_download, state="disabled")
        self.hf_dl_btn.pack(side="left", padx=2)

        # Results
        ttk.Label(self.hf_tab, text="Datasets (click to select):").pack(anchor="w", padx=10)
        rlf = ttk.Frame(self.hf_tab)
        rlf.pack(fill="both", expand=True, padx=10)
        scroll = ttk.Scrollbar(rlf)
        scroll.pack(side="right", fill="y")
        self.hf_list = tk.Listbox(rlf, yscrollcommand=scroll.set)
        self.hf_list.pack(side="left", fill="both", expand=True)
        scroll.config(command=self.hf_list.yview)

        self.hf_datasets = []

        # Log
        ttk.Label(self.hf_tab, text="Log:").pack(anchor="w", padx=10)
        self.hf_log = scrolledtext.ScrolledText(self.hf_tab, height=8, state="disabled")
        self.hf_log.pack(fill="x", padx=10, pady=5)

        self.hf_progress = ttk.Progressbar(self.hf_tab, mode="indeterminate")
        self.hf_progress.pack(fill="x", padx=10)

    def _hf_log(self, msg):
        self.hf_log.configure(state="normal")
        self.hf_log.insert("end", msg + "\n")
        self.hf_log.see("end")
        self.hf_log.configure(state="disabled")
        self.update_idletasks()

    def _hf_search(self):
        self.hf_list.delete(0, "end")
        self.hf_datasets = []
        self.hf_dl_btn.configure(state="disabled")
        q = self.hf_query.get().strip()
        task = self.hf_task.get() or None

        def run():
            self.hf_progress.start()
            self._hf_log(f"Searching HF datasets (query='{q}', task={task or 'any'})...")
            params = f"search={q}&sort=downloads&direction=-1&limit=50"
            if task:
                params += f"&filter={task}"
            try:
                with urlopen(Request(f"{HF_API}/datasets?{params}", headers={"User-Agent": "repo2dataset"}), timeout=15) as r:
                    results = json.loads(r.read())
            except Exception as e:
                self._hf_log(f"Error: {e}")
                self.hf_progress.stop()
                return
            if not results:
                self._hf_log("No datasets found.")
                self.hf_progress.stop()
                return
            for ds in results:
                d = ds.get("cardData") or {}
                desc = (d.get("description", "") or "")[:90]
                tags = ", ".join(ds.get("tags", [])[:3])
                label = f"{ds['id']:45s} {ds.get('downloads', 0):>10,}↓  {tags:25s}  {desc}"
                self.hf_datasets.append(ds)
                self.hf_list.insert("end", label)
            self._hf_log(f"Found {len(results)} datasets. Select one and click Download.")
            self.hf_dl_btn.configure(state="normal")
            self.hf_progress.stop()

        threading.Thread(target=run, daemon=True).start()

    def _hf_download(self):
        sel = self.hf_list.curselection()
        if not sel:
            return
        ds = self.hf_datasets[sel[0]]
        name = ds["id"]

        def run():
            self.hf_progress.start()
            self.hf_dl_btn.configure(state="disabled")
            self._hf_log(f"Downloading {name}...")
            try:
                from datasets import load_dataset
            except ImportError:
                self._hf_log("Need datasets library. Run: pip install datasets")
                self.hf_progress.stop()
                self.hf_dl_btn.configure(state="normal")
                return
            spl = "train"
            try:
                data = load_dataset(name, split=spl, streaming=True)
                out = Path(f"./hf-datasets/{name.replace('/', '_')}")
                out.mkdir(parents=True, exist_ok=True)
                fout = out / f"{spl}.jsonl"
                count = 0
                for row in data:
                    fout.open("a").write(json.dumps(row, default=str) + "\n")
                    count += 1
                self._hf_log(f"Saved {count} rows to {fout}")
            except Exception as e:
                self._hf_log(f"Error: {e}")
            self.hf_progress.stop()
            self.hf_dl_btn.configure(state="normal")

        threading.Thread(target=run, daemon=True).start()

    def _build_dataset_tab(self):
        # -- Input frame --
        f = ttk.LabelFrame(self.build_tab, text="Search Criteria", padding=10)
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

        ttk.Label(f, text="Source:").grid(row=row, column=0, sticky="w")
        self.source = ttk.Combobox(f, values=["github", "hf"], state="readonly", width=17)
        self.source.set("github")
        self.source.grid(row=row, column=1, sticky="ew", padx=5)

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
        bf = ttk.Frame(self.build_tab)
        bf.pack(fill="x", padx=10, pady=5)
        ttk.Button(bf, text="Search Repos", command=self._search).pack(side="left", padx=2)
        self.extract_btn = ttk.Button(bf, text="Build Dataset", command=self._extract, state="disabled")
        self.extract_btn.pack(side="left", padx=2)
        ttk.Button(bf, text="View Manifest", command=self._view_manifest).pack(side="left", padx=2)
        ttk.Button(bf, text="Open Output", command=self._open_output).pack(side="left", padx=2)

        # Repo list
        ttk.Label(self.build_tab, text="Repos (click to select/deselect):").pack(anchor="w", padx=10)
        rlf = ttk.Frame(self.build_tab)
        rlf.pack(fill="both", expand=True, padx=10, pady=2)
        scroll = ttk.Scrollbar(rlf)
        scroll.pack(side="right", fill="y")
        self.repo_list = tk.Listbox(rlf, selectmode="multiple", yscrollcommand=scroll.set)
        self.repo_list.pack(side="left", fill="both", expand=True)
        scroll.config(command=self.repo_list.yview)

        # Status / log
        ttk.Label(self.build_tab, text="Log:").pack(anchor="w", padx=10)
        self.log = scrolledtext.ScrolledText(self.build_tab, height=10, state="disabled")
        self.log.pack(fill="both", padx=10, pady=5)

        # Progress
        self.progress = ttk.Progressbar(self.build_tab, mode="indeterminate")
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
        src = self.source.get() or "github"

        def run():
            self.progress.start()
            self._log(f"Searching {lang} repos (source={src}, stars>{stars})...")
            items = []
            if src == "hf":
                try:
                    from datasets import load_dataset
                    self._log("Loading 40M metadata from HuggingFace...")
                    ds = load_dataset("ibragim-bad/github-repos-metadata-40M", split="sample", streaming=True)
                    for row in ds:
                        if (row.get("language") or "").lower() != lang.lower():
                            continue
                        if (row.get("watchers_count") or 0) < int(stars):
                            continue
                        name = row.get("repo_name") or ""
                        if not name or "/" not in name:
                            continue
                        items.append({
                            "full_name": name,
                            "stargazers_count": row.get("watchers_count") or 0,
                            "description": row.get("description") or "",
                        })
                        if len(items) >= int(limit):
                            break
                    items.sort(key=lambda r: r["stargazers_count"], reverse=True)
                except ImportError:
                    self._log("HuggingFace datasets not installed. Run: pip install datasets")
                    self.progress.stop()
                    return
            else:
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
