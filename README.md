# agent-sessions-vault

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue.svg)
![Platform: Linux](https://img.shields.io/badge/platform-linux%20(systemd)-lightgrey.svg)
![AWS S3](https://img.shields.io/badge/storage-AWS%20S3-orange.svg)

> Automatic backup, searchable catalog and one-command restore for your **Cursor Agent** and **Kiro CLI** sessions — stored in your own private S3 bucket. No LLM tokens spent.

Formatted your machine? Lost `~/.cursor`? Can't remember which session had that fix? `agent-sessions-vault` keeps every session safe and one keystroke away from `--resume`.

## Features

- **Zero tokens** — reads the tools' local files only; no AI calls involved.
- **Automatic** — a `systemd --user` timer backs up new and changed sessions every 15 minutes.
- **Cheap by design** — a local manifest cache means unchanged runs make **zero S3 requests** (not even `LIST`); only changed files are uploaded.
- **Searchable catalog** — date, tool, project and title for every session, browsable with `fzf`.
- **Copy-to-resume** — pick a session and get `cd <project> && agent --resume=<uuid>` in your clipboard.
- **Safe copies** — Cursor's SQLite `store.db` is snapshotted via the SQLite backup API, consistent even while a session is open.
- **Hardened bucket** — private, encrypted, TLS-only and versioned out of the box.
- **Full restore** — rebuild every session on a fresh install with one command.

## Supported tools

| Tool | Resume command | Backed-up paths |
|---|---|---|
| Cursor Agent CLI | `agent --resume=<uuid>` | `~/.cursor/chats/**` |
| Cursor IDE | — (transcripts) | `~/.cursor/projects/*/agent-transcripts/**` |
| Kiro CLI | `kiro-cli --resume-id <uuid>` | `~/.kiro/sessions/**`, `~/.kiro/session-index/*` |

Credentials are never backed up (`~/.aws`, `~/.kiro/secrets.json`, `~/.local/share/kiro-cli/data.sqlite3`).

## Requirements

- Linux with `systemd` (tested on Ubuntu)
- Python 3.8+ (standard library only)
- [AWS CLI v2](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html), authenticated
- [`fzf`](https://github.com/junegunn/fzf)
- `wl-copy` (Wayland) or `xclip` (X11) for clipboard support

## Quick start

```bash
git clone https://github.com/betodoescher/agent-sessions-vault.git
cd agent-sessions-vault

./setup-bucket.sh                                  # creates s3://agent-sessions-vault-<account-id>
./install.sh s3://agent-sessions-vault-<account-id>
source ~/.zshrc                                    # or ~/.bashrc

sessions-vault backup                              # first full backup (optional, the timer will run it)
```

## Usage

```bash
sessions                     # browse the catalog; Enter copies the resume command
sessions-vault backup        # run a backup now
sessions-vault catalog       # rebuild the local catalog only
sessions-vault restore       # download everything from S3 into ~
```

Search the catalog without `fzf`:

```bash
grep -i "migration" ~/.cache/sessions-vault/catalog.tsv | column -t -s$'\t'
```

## How it works

```
~/.cursor, ~/.kiro ──► manifest diff (mtime + size) ──► staging ──► aws s3 cp (PUT only)
                              │
                              └──► catalog.tsv / catalog.jsonl ──► fzf picker
```

1. List session files and compare them against `~/.cache/sessions-vault/manifest.json`.
2. Nothing changed → exit without touching S3.
3. Changed files are staged on disk (SQLite files via the backup API) and uploaded with `aws s3 cp --recursive`, which never lists the bucket.
4. The catalog is rebuilt from each tool's metadata (Cursor `meta.json`, Kiro `sessions/cli/*.json`). Empty sessions are skipped; untitled Cursor sessions fall back to their first prompt.

Bucket layout:

```
s3://<bucket>/
├── catalog.jsonl
└── files/
    ├── .cursor/chats/...
    ├── .cursor/projects/<project>/agent-transcripts/...
    └── .kiro/sessions/...
```

## Configuration

| Setting | Where | Default |
|---|---|---|
| `SESSIONS_VAULT_BUCKET` | `~/.config/sessions-vault/env` | set by `install.sh` |
| `AWS_PROFILE` | `~/.config/sessions-vault/env` | current `AWS_PROFILE` or `default` |
| Backup interval | `./install.sh <bucket> <interval>` | `15min` |
| Bucket name / region | `./setup-bucket.sh <name> <region>` | `agent-sessions-vault-<account-id>` / `us-east-1` |

## Restore on a new machine

```bash
# install aws cli + fzf, then authenticate: aws configure
git clone https://github.com/betodoescher/agent-sessions-vault.git
cd agent-sessions-vault
./install.sh s3://agent-sessions-vault-<account-id>
sessions-vault restore
```

Restore downloads every file to its original path and rebuilds the local cache, so the next backup uploads nothing.

> **Note:** Cursor ties sessions to the project path. Keep the same username and project paths (`/home/<user>/...`) for `--resume` to work.

## Security

| Control | Setting |
|---|---|
| Public access | Block Public Access (all four flags) |
| Encryption at rest | SSE-S3 (AES-256) with bucket key |
| Encryption in transit | Bucket policy denies non-TLS requests |
| Ownership | `BucketOwnerEnforced` (ACLs disabled) |
| Versioning | Enabled; 5 noncurrent versions kept for 7 days |
| Local config | `~/.config/sessions-vault/env` with `chmod 600` |

Sessions may contain source code and anything pasted into chats. Store them only in an AWS account you are allowed to use for that data.

## Cost

Typical usage (~1 GB of sessions) costs **a few cents per month** in `us-east-1`: S3 Standard storage plus a handful of `PUT` requests for active sessions. Idle runs are free.

## Troubleshooting

| Problem | Fix |
|---|---|
| Check timer status | `systemctl --user list-timers sessions-vault.timer` |
| See backup logs | `journalctl --user -u sessions-vault -n 20` |
| `SESSIONS_VAULT_BUCKET not set` | Re-run `./install.sh s3://<bucket>` |
| `fzf not found` | `sudo apt install fzf` |
| Force a full re-upload | `rm ~/.cache/sessions-vault/manifest.json && sessions-vault backup` |
| `--resume` can't find a restored Cursor session | Restore under the same username and project paths |

## Uninstall

```bash
systemctl --user disable --now sessions-vault.timer
rm ~/.config/systemd/user/sessions-vault.{service,timer} ~/.local/bin/sessions-vault
rm -r ~/.config/sessions-vault ~/.cache/sessions-vault
# remove the "alias sessions=..." line from ~/.zshrc / ~/.bashrc
```

The S3 bucket is kept; delete it manually if you no longer need the backups.

## Roadmap

- [ ] macOS support (`launchd`)
- [ ] Claude Code / Codex CLI sessions
- [ ] Optional client-side encryption

## Contributing

Issues and pull requests are welcome. Keep changes small, dependency-free (Python standard library + AWS CLI) and include a short test plan in the PR description.

## License

[MIT](LICENSE) © Klelber Xavier
