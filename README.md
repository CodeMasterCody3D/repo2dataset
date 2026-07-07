# repo2dataset — GitHub Repos → AI Training Datasets

Turn any GitHub repo into structured training data for fine-tuning code
models. Extracts **full source code** + **bug-fix PRs** + **fix commits**
into one JSONL corpus.

## Quickstart

```bash
pip install requests

# Interactive mode (asks you questions)
python src/repo2dataset.py tui

# Search for repos
python src/repo2dataset.py search --language python --domain cli --limit 5

# Extract from a single repo
python src/repo2dataset.py extract --repo gruns/icecream --output ./my-dataset

# Full pipeline: search + extract from top repos
python src/repo2dataset.py all --language rust --domain cli --limit 3 --output ./rust-dataset
```

## Dataset Format

Each line in `dataset.jsonl` is a JSON object:

**Code examples:**
```jsonl
{"instruction": "Write the file `src/main.rs` from owner/repo", "response": "<full file contents>", "repo": "owner/repo", "source": "code", "path": "src/main.rs"}
```

**Bug-fix examples:**
```jsonl
{"instruction": "PR title + description of the bug", "response": "```diff\n...patch...\n```", "repo": "owner/repo", "source": "pr", "ref": "#240"}
```

## Commands

| Command  | Description |
|----------|-------------|
| `search` | Find top repos by language + domain |
| `extract`| Build dataset from one repo |
| `all`    | Search + extract from multiple repos |
| `tui`    | Interactive questionnaire (asks language, domain, etc.) |

## Repo Sources

| Source   | Description |
|----------|-------------|
| `github` | GitHub API search (default). Set `GITHUB_TOKEN` for higher rate limits. |
| `hf`     | [40M repo metadata dataset](https://huggingface.co/datasets/ibragim-bad/github-repos-metadata-40M) on HuggingFace. Requires `pip install datasets`. No rate limits, fast filtering. |

Use `--source hf` on `search` or `all` commands:

```bash
python src/repo2dataset.py search --source hf --language rust --min-stars 5000 --limit 10
python src/repo2dataset.py all --source hf --language python --domain cli --output ./dataset
```

## Auth

Set `GITHUB_TOKEN` to increase API rate limits (5000 req/hr vs 60 unauthed):

```bash
export GITHUB_TOKEN="ghp_..."
```

## Dependencies

- Python 3.8+
- `requests` (for GitHub API)
- `datasets` (optional, for HuggingFace source)
- `git` (for cloning repos)
