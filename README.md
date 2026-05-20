# llm-tg-bot

A Python Telegram bot that bridges chat messages to local CLI agents like `codex`, `claude`, `gemini`, and `agy`. It uses a headless request/response model, rendering provider replies as rich text while keeping system messages in plain text.

## Features

- **Multi-Provider Support**: Supports `codex`, `claude`, `gemini`, and `agy` with per-chat logical sessions.
- **Request Queueing**: Queues incoming messages when the provider is busy.
- **Smart Formatting**: Converts Markdown to Telegram-safe HTML with automatic message splitting.
- **Access Control**: User allowlist with numeric Telegram IDs.
- **Session Management**: Automatic idle cleanup and fresh session creation via `/new`.

## Quick Start

1. **Install**:
   ```bash
   # Create and activate virtual environment
   python3 -m venv .venv
   source .venv/bin/activate

   # Install the package in editable mode
   pip install -e .
   ```
2. **Configure**:
   ```bash
   cp .env.example .env
   # Edit .env with your TELEGRAM_BOT_TOKENS and TELEGRAM_ALLOWED_USER_IDS
   ```
3. **Run**:
   ```bash
   # Ensure venv is activated (source .venv/bin/activate)
   llm-tg-bot
   ```

## Configuration

Key variables in `.env`:
- `TELEGRAM_BOT_TOKENS`: Your bot's API token(s). Comma-separate multiple tokens for multi-bot support.
- `TELEGRAM_ALLOWED_USER_IDS`: Comma-separated user IDs (use `*` for open access in dev).
- `WORKDIR`: Shared root for providers. `/new` lets you select subdirectories.
- `DEFAULT_PROVIDER`: Default CLI to use (e.g., `codex`).
- `SESSION_IDLE_TIMEOUT_SECONDS`: Closes idle sessions (default: 60m).

## Deployment

For production, use a process manager like **Supervisor**. See `deploy/llm-tg-bot.supervisor.example` for a template.

1. Install Supervisor: `sudo apt install supervisor`
2. Copy the template: `sudo cp deploy/llm-tg-bot.supervisor.example /etc/supervisor/conf.d/llm-tg-bot.conf`
3. Edit the config (update `user`, `directory`, `command`, and `environment`).
4. Apply changes:
   ```bash
   sudo supervisorctl reread
   sudo supervisorctl update
   ```

## Telegram Commands

- `/new [provider] [dir]` — Start a fresh session in a specific directory.
- `/use <provider>` — Switch the current chat's provider.
- `/stop` — Terminate and forget the current session.
- `/cancel` — Interrupt the in-flight request or abort `/new` setup.
- `/status` — View current session and queue status.
- `/list` — List available providers and working directories.

## Notes

- **Permissions**: Providers run in "yolo" / auto-approve mode, inheriting the bot process's OS permissions. **Always run the bot as a normal, unprivileged user.**
- **Codex**: Defaults to `--skip-git-repo-check`. Set `CODEX_SKIP_GIT_REPO_CHECK=0` to require valid Git trees.
