# 🦀 gemclaw

> Claude Code, but the claw is holding a gem.

A tiny proxy that makes [Claude Code](https://claude.com/claude-code) talk to **Gemini 3.1 Pro** instead of Anthropic's API. That's it. That's the whole pitch.

## Design goal (read this part)

gemclaw exists for exactly **one** purpose: let Claude Code run against **Gemini 3.1 Pro** as the backend.

- Not a general-purpose LLM router.
- Not a LiteLLM replacement.
- Not trying to support OpenAI, Groq, Claude 3.5, Gemini Flash, your cousin's homebrew model, etc.
- No model selection. No fallbacks. No magic.

If you want multi-provider routing, go use LiteLLM. This repo is the smallest possible bridge between *this specific CLI* and *this specific model*.

## Requirements

- Python 3.10+
- [`uv`](https://github.com/astral-sh/uv) (you should already have this)
- A Gemini API key from [Google AI Studio](https://aistudio.google.com/app/apikey)

## Install

```console
$ uv tool install git+https://github.com/xeb/gemclaw
Resolved 38 packages in 243ms
Installed 1 package in 23ms
 + gemclaw==0.1.0
Installed 1 executable: gemclaw
```

> Note the `git+` prefix. Without it, `uv` will yell at you.

## Run

```bash
export GEMINI_API_KEY="AIza...your-key-here..."
gemclaw
```

That's it. gemclaw spins up a tiny local proxy, sets the right env vars for Claude Code, and launches it. When Claude Code exits, the proxy cleans itself up. No background processes to babysit.

## ⚠️ Heads up: gemclaw temporarily sidelines your Claude Code login

**Read this if you're already logged into Claude Code.** If you are, there's an OAuth token at `~/.claude/.credentials.json`. When Claude Code sees that file AND our injected `ANTHROPIC_API_KEY`, it prints an annoying **"Auth conflict"** warning on every start. Worse, it can sometimes pick the cached OAuth token and bypass our proxy entirely — meaning you *think* you're talking to Gemini but you're quietly billing your Anthropic account.

To keep the UI clean and make sure your requests actually reach the Gemini proxy, at startup **gemclaw renames your credentials file**:

```
~/.claude/.credentials.json   →   ~/.claude/.credentials.json.gemclaw-sidelined
```

On exit (normal quit, `Ctrl-C`, or `kill`) it **renames it back**. Your login survives.

### What if something goes catastrophically wrong?

If gemclaw gets `kill -9`'d or your machine loses power mid-session, the rename won't auto-undo. You'll see this when you run plain `claude` and it thinks you're logged out. Fix with one line:

```bash
mv ~/.claude/.credentials.json.gemclaw-sidelined ~/.claude/.credentials.json
```

(or `credentials.json` without the leading dot, depending on your Claude Code version). That's it. No data is destroyed — the file is literally just renamed.

### Things gemclaw does NOT touch

- `CLAUDE_CONFIG_DIR` — left alone entirely.
- Any other file in `~/.claude/` — settings, MCP config, history, etc.
- Your keyring / macOS Keychain — if your credentials live there instead of a file, gemclaw can't move them and you'll see the "Auth conflict" warning. Harmless — just noisy.

## What you'll see

```
 ▐▛███▜▌   Claude Code v2.1.116
▝▜█████▛▘  gemini-3.1-pro-preview · Claude Max
  ▘▘ ▝▝    /home/you/some-project

❯ List everything in this directory and tell me what kind of project it looks like.

● Here are the contents of the current directory:

  - drafts/       (Directory)
  - notes/        (Directory)
  - recipe.txt    (Empty file)
  - shopping.txt  (Empty file)
  - todo.md       (Empty file)

  Based on these files and folders, this doesn't look like a software project.
  Instead, it appears to be a personal organization or note-taking directory.
  It has placeholders for daily life activities like cooking (recipe.txt),
  errands (shopping.txt, todo.md), and general writing or ideas.
```

The UI is Claude Code. The brain is Gemini. The claw is holding a gem. You get it.

## Flags

| Flag | What it does |
|---|---|
| `--verbose` | Noisy mode. Useful when things break. |
| `--quiet` | Even quieter than default (WARNING level). |
| `--port N` | Pin the proxy to port `N` instead of auto-picking. |
| `--config` | Print configuration and exit. |
| `--version` | Print version and exit. |

Logs land in `~/.gemproxy/logs/` and the paths are printed when gemclaw exits, so you don't have to go hunting.

## When it breaks

1. Run it again with `--verbose`.
2. Check the two log files it prints on exit (CLI log + proxy log).
3. Open an issue with those log contents.

## License

MIT. Do whatever.
