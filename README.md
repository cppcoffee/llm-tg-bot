# llm-tg-bot

A Python Telegram bot that bridges chat messages to local CLI agents like `codex`, `opencode`, and `pi`. It uses a headless request/response model, rendering provider replies as rich text while keeping system messages in plain text.

## Features

- **Multi-Provider Support**: Supports `codex`, `opencode`, and `pi` with per-chat logical sessions.
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

The bot loads `.env` from the project root (the parent of the `llm_tg_bot` package), regardless of the current working directory. Existing environment variables take precedence; restart the bot after changing `.env`.

Key variables in `.env`:

- `TELEGRAM_BOT_TOKENS`: Your bot's API token(s). Comma-separate multiple tokens for multi-bot support.
- `TELEGRAM_ALLOWED_USER_IDS`: Comma-separated user IDs (use `*` for open access in dev).
- `WORKDIR`: Shared root for providers. `/new` lets you select subdirectories.
- `DEFAULT_PROVIDER`: Default CLI to use (e.g., `codex`, `opencode`, or `pi`).
- `SESSION_IDLE_TIMEOUT_SECONDS`: Closes idle sessions (default: 60m).

## Deployment

For production, you can use **Systemd** or **Supervisor**.

### Option A: One-click script (recommended)

`deploy/manage.sh` creates the virtualenv, installs dependencies, writes the service config, and starts the service. It auto-detects the backend: **systemd** when it is running, otherwise **Supervisor** (install it first with `sudo apt install supervisor`). Run it as a normal user; it uses `sudo` for system-level steps.

```bash
./deploy/manage.sh install          # venv + deps + service, then start
./deploy/manage.sh update           # git pull + reinstall deps + restart the service
./deploy/manage.sh status           # systemctl status or supervisorctl status
./deploy/manage.sh logs             # journalctl -f or tail -f log/llm-tg-bot.log
./deploy/manage.sh uninstall        # stop + remove the service config (.env and .venv kept)
./deploy/manage.sh uninstall --purge  # also delete .venv
```

Force a backend when both are installed:

```bash
BACKEND=supervisor ./deploy/manage.sh install
BACKEND=systemd    ./deploy/manage.sh install
```

On first install it copies `.env.example` to `.env` and stops before starting the bot; edit `.env` (tokens, allowed user IDs) and start it with `sudo systemctl start llm-tg-bot` (systemd) or `sudo supervisorctl start llm-tg-bot` (Supervisor).

With Supervisor the service log is written to `log/llm-tg-bot.log` in the project directory and the config is `/etc/supervisor/conf.d/llm-tg-bot.conf`.

### Option B: Supervisor (manual)

See `deploy/llm-tg-bot.supervisor.example` for a template.

1. Install Supervisor: `sudo apt install supervisor`
2. Copy the template: `sudo cp deploy/llm-tg-bot.supervisor.example /etc/supervisor/conf.d/llm-tg-bot.conf`
3. Edit the config (update `user`, `directory`, `command`, and `environment`).
4. Apply changes:
   ```bash
   sudo supervisorctl reread
   sudo supervisorctl update
   ```

### Option C: Systemd (manual)

See `deploy/llm-tg-bot.service.example` for a service file template.

1. Copy the template: `sudo cp deploy/llm-tg-bot.service.example /etc/systemd/system/llm-tg-bot.service`
2. Edit the service file (update paths, `User`, `WorkingDirectory`, and ensure the `PATH` environment variable contains your virtual environment's bin folder and the directory of your local CLI agents like pi or opencode).
3. Reload systemd daemon and enable/start the service:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable llm-tg-bot.service
   sudo systemctl start llm-tg-bot.service
   ```
4. View service status and logs:
   ```bash
   sudo systemctl status llm-tg-bot.service
   sudo journalctl -u llm-tg-bot.service -f
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
- **opencode**: Runs in headless mode via `opencode run --format json` with `--dangerously-skip-permissions`. Resumes sessions with `--session <id>`.
- **pi**: Runs in headless mode via `pi --print --mode json --approve`. Resumes sessions with `--session <id>`; sessions are grouped by the working directory.
