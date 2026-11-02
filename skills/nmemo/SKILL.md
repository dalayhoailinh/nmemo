---
name: nmemo
description: Local, lossless memory of every past session (the user's messages, your replies, commands you ran, tool results, full contents of files you wrote). Use when the user refers to earlier work or asks what was said, done, decided or written before ("last time", "yesterday", "that file we made", "lần trước", "hôm qua mình làm gì", "nhớ lại", "tìm lại", "đọc nguyên văn"), before redoing work that may already exist, or to recover an older version of a file.
---

# nmemo — bộ nhớ cục bộ

Every turn is recorded automatically by hooks into `~/.nmemo/nmemo.db` (override with `NMEMO_HOME`):
the user's prompts, your replies and progress notes, every tool call with its full input (shell
commands, file writes and edits), tool results, and a snapshot of each file after you wrote it or a
command changed it. Nothing leaves the machine unless you read it through these tools.

## Tools (MCP server `nmemo`)

| Tool | Use it to |
|---|---|
| `sessions` | See the map first: past sessions with time, folder, title, counts |
| `search` | Find words or "an exact phrase" anywhere; accents optional (`duong` finds `đường`). Filter with `kind` (user, assistant, tool_call, tool_result, file, note…), `session`, `since`/`until`, `path` |
| `timeline` | Replay one session in order: what was asked, answered, run and written |
| `read` | Get exact content: `i<n>` an item, `e<n>` the raw transcript line byte-for-byte, `s<n>` a session overview; `around` adds neighbours |
| `remember` | Save a durable note the user wants kept |

## How to work with it

1. Go from broad to exact: `sessions` or `search` with a few words, then `read` only the ids you need.
2. Results say how many hits exist. When the user wants *all* of something, page with `offset`
   until you have seen every hit, and say so.
3. Quote earlier content only from `read` output and cite its id (`i123`). Never rebuild long text,
   code or file contents from your own recollection.
4. To restore an earlier file version: `search` with `kind: ["file"]` and `path`, `read` the version,
   then write it back.
5. If the MCP tools are missing, use the CLI that ships with this plugin (two folders above this
   skill's base directory): `python3 <plugin>/bin/nmemo search "words"`, `read i123`, `sessions`, `timeline s4`.
