#!/usr/bin/env python3
"""Backup, catalog and restore Cursor Agent and Kiro CLI sessions to S3.

  sessions-vault backup    catalog + upload only new/changed files to S3
  sessions-vault pick      pick a session (fzf) and copy its resume command
  sessions-vault restore   download everything from S3 into ~ (new/reinstalled machine)
  sessions-vault catalog   rebuild the local catalog only
  sessions-vault open      show the full catalog as a table in $PAGER (default: less -S)

Config: SESSIONS_VAULT_BUCKET (e.g. s3://my-bucket) and optional AWS_PROFILE,
read from ~/.config/sessions-vault/env.
"""
import json, os, shlex, shutil, signal, sqlite3, subprocess, sys, tempfile
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
CONFIG = HOME / ".config/sessions-vault/env"
CACHE = HOME / ".cache/sessions-vault"
MANIFEST = CACHE / "manifest.json"
CATALOG = CACHE / "catalog.jsonl"
CATALOG_TSV = CACHE / "catalog.tsv"

# (base dir relative to ~, glob) — what gets backed up
SOURCES = [
    (".cursor/chats", "**/*"),
    (".cursor/projects", "*/agent-transcripts/**/*"),
    (".kiro/sessions", "**/*"),
    (".kiro/session-index", "*"),
]
# deliberately excluded: ~/.local/share/kiro-cli/data.sqlite3 holds auth tokens, not conversations
SKIP_SUFFIXES = ("-wal", "-shm", "-journal", ".lock")


def load_config():
    if CONFIG.exists():
        for line in CONFIG.read_text().splitlines():
            key, sep, value = line.strip().partition("=")
            if sep and not key.startswith("#"):
                os.environ.setdefault(key, value.strip().strip('"'))
    bucket = os.environ.get("SESSIONS_VAULT_BUCKET", "").rstrip("/")
    if not bucket.startswith("s3://"):
        sys.exit(f"SESSIONS_VAULT_BUCKET not set (s3://...) in {CONFIG}")
    return bucket


def aws(*args):
    subprocess.run(["aws", *args, "--only-show-errors"], check=True)


# ---------- catalog ----------

def iso(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat() if ms else None


def clean(text, size=100):
    text = " ".join((text or "").split())
    return text if len(text) <= size else text[: size - 1] + "…"


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def cursor_sessions():
    for meta_path in (HOME / ".cursor/chats").glob("*/*/meta.json"):
        meta = read_json(meta_path) or {}
        prompts = read_json(meta_path.parent / "prompt_history.json") or []
        title = meta.get("title") or (prompts[-1] if prompts else None)  # list is newest → oldest
        if not title:
            continue
        uid = meta_path.parent.name
        yield {"tool": "cursor", "id": uid, "title": clean(title), "cwd": meta.get("cwd"),
               "created": iso(meta.get("createdAtMs")), "updated": iso(meta.get("updatedAtMs")),
               "resume": f"agent --resume={uid}"}


def kiro_sessions():
    for path in (HOME / ".kiro/sessions/cli").glob("*.json"):
        meta = read_json(path) or {}
        if not meta.get("title"):  # no title = session without messages
            continue
        uid = meta.get("session_id", path.stem)
        yield {"tool": "kiro", "id": uid, "title": clean(meta["title"]), "cwd": meta.get("cwd"),
               "created": meta.get("created_at"), "updated": meta.get("updated_at"),
               "resume": f"kiro-cli --resume-id {uid}"}


def build_catalog():
    rows = sorted([*cursor_sessions(), *kiro_sessions()], key=lambda r: r["updated"] or "", reverse=True)
    for row in rows:
        row["project"] = Path(row["cwd"] or "?").name
        row["command"] = f"cd {shlex.quote(row['cwd'])} && {row['resume']}" if row["cwd"] else row["resume"]
    CACHE.mkdir(parents=True, exist_ok=True)
    CATALOG.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    CATALOG_TSV.write_text("".join(
        f"{(r['updated'] or '')[:16].replace('T', ' ')}\t{r['tool']}\t{r['project']}\t{r['title']}\t{r['command']}\n"
        for r in rows))
    return len(rows)


# ---------- incremental backup ----------

def local_files():
    for base, pattern in SOURCES:
        for path in (HOME / base).glob(pattern):
            if path.is_file() and not path.name.endswith(SKIP_SUFFIXES):
                yield path


def fingerprint(path):
    st = path.stat()
    return [st.st_mtime_ns, st.st_size]


def stage(path, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".db":
        # SQLite backup API: consistent copy even while the session is open (WAL)
        src = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        with sqlite3.connect(dest) as out:
            src.backup(out)
        src.close()
    else:
        shutil.copy2(path, dest)


def backup():
    bucket = load_config()
    total = build_catalog()
    manifest = read_json(MANIFEST) or {}
    current = {str(p.relative_to(HOME)): fingerprint(p) for p in local_files()}
    changed = [rel for rel, fp in current.items() if manifest.get(rel) != fp]
    if not changed:
        print(f"{total} sessions in catalog; nothing new, no S3 requests")
        return

    # stage on disk: /tmp is often tmpfs (counts as RAM)
    with tempfile.TemporaryDirectory(prefix="staging-", dir=CACHE) as tmp:
        staging = Path(tmp)
        for rel in changed:
            try:
                stage(HOME / rel, staging / "files" / rel)
            except (OSError, sqlite3.Error) as err:
                print(f"skipped {rel}: {err}", file=sys.stderr)
                current.pop(rel)
        shutil.copy2(CATALOG, staging / "catalog.jsonl")
        # local→S3 cp never lists the bucket: only PUTs for changed files
        aws("s3", "cp", str(staging), bucket, "--recursive", "--sse", "AES256")

    manifest.update(current)
    MANIFEST.write_text(json.dumps(manifest))
    print(f"{total} sessions in catalog; {len(changed)} file(s) uploaded")


# ---------- restore ----------

def restore():
    bucket = load_config()
    for base, _ in SOURCES:
        aws("s3", "sync", f"{bucket}/files/{base}", str(HOME / base))
    CACHE.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps({str(p.relative_to(HOME)): fingerprint(p) for p in local_files()}))
    print(f"restore complete; {build_catalog()} sessions in catalog")


# ---------- pick ----------

def copy_to_clipboard(text):
    for cmd in (["wl-copy"], ["xclip", "-selection", "clipboard"]):
        if shutil.which(cmd[0]):
            subprocess.run(cmd, input=text.encode(), check=False)
            return True
    return False


def pick():
    if not CATALOG_TSV.exists():
        build_catalog()
    if not shutil.which("fzf"):
        sys.exit("fzf not found: install it (e.g. sudo apt install fzf)")
    result = subprocess.run(
        ["fzf", "--delimiter=\t", "--with-nth=1,2,3,4", "--tiebreak=index", "--no-hscroll",
         "--header=date | tool | project | title   (Enter copies the command)",
         "--preview=echo {5}", "--preview-window=down,3,wrap"],
        stdin=CATALOG_TSV.open(), stdout=subprocess.PIPE, text=True)
    if result.returncode != 0 or not result.stdout.strip():
        return
    command = result.stdout.rstrip("\n").split("\t")[4]
    copied = copy_to_clipboard(command)
    print(command + ("\n(copied to clipboard)" if copied else ""))


def open_catalog():
    build_catalog()
    rows = [["DATE", "TOOL", "PROJECT", "TITLE", "COMMAND"]]
    rows += [line.split("\t") for line in CATALOG_TSV.read_text().splitlines()]
    widths = [max(len(r[i]) for r in rows) for i in range(4)]
    text = "\n".join("  ".join(c.ljust(w) for c, w in zip(r, widths)) + "  " + r[4] for r in rows) + "\n"
    if not sys.stdout.isatty():
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)  # quiet exit when piped to head/grep
        sys.stdout.write(text)
        return
    subprocess.run(shlex.split(os.environ.get("PAGER", "less -S")), input=text, text=True, check=False)


if __name__ == "__main__":
    actions = {"backup": backup, "restore": restore, "pick": pick, "open": open_catalog,
               "catalog": lambda: print(build_catalog())}
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    if action not in actions:
        sys.exit(__doc__)
    actions[action]()
