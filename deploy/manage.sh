#!/usr/bin/env bash
# One-click deploy helper for llm-tg-bot.
#
# Picks systemd when it is running, otherwise Supervisor.
#
# Usage: deploy/manage.sh <install|uninstall|status|logs> [options]
# Run as a normal user; the script uses sudo for system-level steps.
set -euo pipefail

SERVICE_NAME="llm-tg-bot"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
SYSTEMD_TEMPLATE="$SCRIPT_DIR/llm-tg-bot.service.example"
SUPERVISOR_TEMPLATE="$SCRIPT_DIR/llm-tg-bot.supervisor.example"
UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
SUPERVISOR_CONF="/etc/supervisor/conf.d/${SERVICE_NAME}.conf"
LOG_DIR="$PROJECT_DIR/log"
LOG_FILE="$LOG_DIR/${SERVICE_NAME}.log"
PYTHON="${PYTHON:-python3}"

usage() {
  cat <<EOF
Usage: $(basename "$0") <command> [options]

Commands:
  install              Create venv, install deps, set up and start the service
  update               Pull latest source, reinstall deps, restart the service
  uninstall [--purge]  Stop and remove the service (--purge also deletes .venv)
  status               Show service status
  logs [args...]       Follow the service log (extra args go to journalctl)

Backend:
  Uses systemd when it is running, otherwise Supervisor. Force one with:
    BACKEND=systemd    $(basename "$0") install
    BACKEND=supervisor $(basename "$0") install

Environment:
  PYTHON               Python interpreter for the venv (default: python3)
EOF
}

die() { echo "error: $*" >&2; exit 1; }

run_root() {
  if [[ $EUID -eq 0 ]]; then "$@"; else sudo "$@"; fi
}

# Run a command as the invoking user even when the script itself runs via sudo.
as_user() {
  if [[ $EUID -eq 0 && -n "${SUDO_USER:-}" ]]; then
    sudo -u "$SUDO_USER" -H "$@"
  else
    "$@"
  fi
}

# PATH of the invoking user's login shell so provider CLIs are discoverable.
user_path() {
  if [[ -n "${SUDO_USER:-}" ]]; then
    sudo -u "$SUDO_USER" bash -lc 'printf %s "$PATH"' 2>/dev/null || printf %s "$PATH"
  else
    printf %s "$PATH"
  fi
}

resolve_user() {
  if [[ -n "${SUDO_USER:-}" ]]; then
    RUN_USER="$SUDO_USER"
  else
    RUN_USER="$(id -un)"
  fi
  RUN_GROUP="$(id -gn "$RUN_USER")"
  RUN_HOME="$(getent passwd "$RUN_USER" 2>/dev/null | cut -d: -f6 || true)"
  [[ -n "$RUN_HOME" ]] || RUN_HOME="$(eval echo "~$RUN_USER")"
}

service_path() {
  printf '%s/.venv/bin:%s' "$PROJECT_DIR" "$(user_path)"
}

detect_backend() {
  if [[ -n "${BACKEND:-}" ]]; then
    case "$BACKEND" in
      systemd|supervisor) printf '%s' "$BACKEND" ;;
      *) die "BACKEND must be 'systemd' or 'supervisor'" ;;
    esac
    return
  fi
  if command -v systemctl >/dev/null 2>&1 \
    && { [[ "$(cat /proc/1/comm 2>/dev/null)" == "systemd" ]] || [[ -d /run/systemd/system ]]; }; then
    printf 'systemd'
  elif command -v supervisorctl >/dev/null 2>&1; then
    printf 'supervisor'
  else
    die "neither a running systemd nor supervisor was found"
  fi
}

require_backend_tools() {
  case "$BACKEND" in
    systemd)    command -v systemctl >/dev/null 2>&1 || die "systemctl not found" ;;
    supervisor) command -v supervisorctl >/dev/null 2>&1 || die "supervisorctl not found; install supervisor first" ;;
  esac
}

setup_env_file() {
  local env_file="$PROJECT_DIR/.env"
  if [[ -f "$env_file" ]]; then
    return
  fi
  cp "$PROJECT_DIR/.env.example" "$env_file"
  echo "Created $env_file from .env.example."
}

setup_venv() {
  if [[ ! -x "$PROJECT_DIR/.venv/bin/python" ]]; then
    echo "Creating virtualenv at $PROJECT_DIR/.venv ..."
    as_user "$PYTHON" -m venv "$PROJECT_DIR/.venv"
  fi
  echo "Installing package ..."
  as_user "$PROJECT_DIR/.venv/bin/pip" install --quiet --upgrade pip
  as_user "$PROJECT_DIR/.venv/bin/pip" install --quiet -e "$PROJECT_DIR"
}

env_needs_editing() {
  grep -q "replace-me" "$PROJECT_DIR/.env"
}

require_installed() {
  case "$BACKEND" in
    systemd)    [[ -f "$UNIT_PATH" ]] || die "$SERVICE_NAME is not installed; run '$(basename "$0") install' first" ;;
    supervisor) [[ -f "$SUPERVISOR_CONF" ]] || die "$SERVICE_NAME is not installed; run '$(basename "$0") install' first" ;;
  esac
}

update_source() {
  if git -C "$PROJECT_DIR" rev-parse --git-dir >/dev/null 2>&1; then
    echo "Pulling latest source ..."
    as_user git -C "$PROJECT_DIR" pull --ff-only
  else
    echo "Not a git checkout; skipping source update."
  fi
}

# --- systemd ----------------------------------------------------------------

render_unit() {
  sed \
    -e "s|^User=.*|User=${RUN_USER}|" \
    -e "s|^Group=.*|Group=${RUN_GROUP}|" \
    -e "s|^WorkingDirectory=.*|WorkingDirectory=${PROJECT_DIR}|" \
    -e "s|^ExecStart=.*|ExecStart=${PROJECT_DIR}/.venv/bin/llm-tg-bot|" \
    -e "s|^Environment=\"PATH=.*|Environment=\"PATH=$(service_path)\"|" \
    "$SYSTEMD_TEMPLATE"
}

install_systemd() {
  [[ -f "$SYSTEMD_TEMPLATE" ]] || die "missing service template: $SYSTEMD_TEMPLATE"
  local tmp
  tmp="$(mktemp)"
  render_unit >"$tmp"
  run_root install -m 644 "$tmp" "$UNIT_PATH"
  rm -f "$tmp"
  run_root systemctl daemon-reload
  run_root systemctl enable "$SERVICE_NAME"
  if env_needs_editing; then
    echo "Service enabled but not started: edit $PROJECT_DIR/.env, then run:"
    echo "  sudo systemctl start $SERVICE_NAME"
  else
    run_root systemctl restart "$SERVICE_NAME"
    echo "Installed and started $SERVICE_NAME (systemd)."
  fi
}

uninstall_systemd() {
  run_root systemctl disable --now "$SERVICE_NAME" 2>/dev/null || true
  run_root rm -f "$UNIT_PATH"
  run_root systemctl daemon-reload
}

status_systemd()   { run_root systemctl status "$SERVICE_NAME" --no-pager || true; }
logs_systemd()     { run_root journalctl -u "$SERVICE_NAME" -f "$@"; }

# --- supervisor -------------------------------------------------------------

render_supervisor_conf() {
  sed \
    -e "s|^user=.*|user=${RUN_USER}|" \
    -e "s|^directory=.*|directory=${PROJECT_DIR}|" \
    -e "s|^command=.*|command=${PROJECT_DIR}/.venv/bin/python -m llm_tg_bot.main|" \
    -e "s|^stdout_logfile=.*|stdout_logfile=${LOG_FILE}|" \
    -e "s|^environment=.*|environment=PYTHONUNBUFFERED=\"1\",HOME=\"${RUN_HOME}\",PATH=\"$(service_path)\"|" \
    "$SUPERVISOR_TEMPLATE"
}

supervisor_reachable() {
  run_root supervisorctl status >/dev/null 2>&1
}

install_supervisor() {
  [[ -f "$SUPERVISOR_TEMPLATE" ]] || die "missing supervisor template: $SUPERVISOR_TEMPLATE"
  supervisor_reachable || die "supervisor daemon not reachable; start it first (e.g. 'sudo service supervisor start')"

  run_root install -d -o "$RUN_USER" -g "$RUN_GROUP" "$LOG_DIR"
  local tmp
  tmp="$(mktemp)"
  render_supervisor_conf >"$tmp"
  run_root install -m 644 "$tmp" "$SUPERVISOR_CONF"
  rm -f "$tmp"

  run_root supervisorctl reread
  run_root supervisorctl update
  if env_needs_editing; then
    run_root supervisorctl stop "$SERVICE_NAME" >/dev/null 2>&1 || true
    echo "Config installed but stopped: edit $PROJECT_DIR/.env, then run:"
    echo "  sudo supervisorctl start $SERVICE_NAME"
  else
    run_root supervisorctl restart "$SERVICE_NAME" >/dev/null 2>&1 \
      || run_root supervisorctl start "$SERVICE_NAME" >/dev/null 2>&1 \
      || true
    echo "Installed and started $SERVICE_NAME (supervisor)."
  fi
}

uninstall_supervisor() {
  if supervisor_reachable; then
    run_root supervisorctl stop "$SERVICE_NAME" >/dev/null 2>&1 || true
  fi
  run_root rm -f "$SUPERVISOR_CONF"
  if supervisor_reachable; then
    run_root supervisorctl reread
    run_root supervisorctl update
  fi
}

status_supervisor() {
  run_root supervisorctl status "$SERVICE_NAME" || true
}

logs_supervisor() {
  [[ -f "$LOG_FILE" ]] || die "log file not found yet: $LOG_FILE"
  tail -n 100 -f "$LOG_FILE"
}

# --- command dispatch -------------------------------------------------------

do_install() {
  resolve_user
  setup_env_file
  if env_needs_editing; then
    echo "WARNING: $PROJECT_DIR/.env still contains 'replace-me'. Edit it before the bot can start."
  fi
  setup_venv
  case "$BACKEND" in
    systemd)    install_systemd ;;
    supervisor) install_supervisor ;;
  esac
  do_status
  echo
  echo "Logs: $(basename "$0") logs"
}

do_update() {
  resolve_user
  require_installed
  update_source
  setup_venv
  case "$BACKEND" in
    systemd)    install_systemd ;;
    supervisor) install_supervisor ;;
  esac
  do_status
}

do_uninstall() {
  resolve_user
  local purge=0
  for arg in "$@"; do
    case "$arg" in
      --purge) purge=1 ;;
      *) die "unknown option for uninstall: $arg" ;;
    esac
  done

  case "$BACKEND" in
    systemd)    uninstall_systemd ;;
    supervisor) uninstall_supervisor ;;
  esac
  echo "Removed $SERVICE_NAME."

  if [[ $purge -eq 1 ]]; then
    rm -rf "$PROJECT_DIR/.venv"
    echo "Removed $PROJECT_DIR/.venv. (.env kept: contains secrets.)"
  fi
}

do_status() {
  case "$BACKEND" in
    systemd)    status_systemd ;;
    supervisor) status_supervisor ;;
  esac
}

do_logs() {
  case "$BACKEND" in
    systemd)    logs_systemd "$@" ;;
    supervisor) logs_supervisor ;;
  esac
}

command="${1:-}"
shift || true
case "$command" in
  install|update|uninstall|status|logs)
    BACKEND="$(detect_backend)"
    require_backend_tools
    ;;
esac

case "$command" in
  install)   do_install "$@" ;;
  update)    do_update "$@" ;;
  uninstall) do_uninstall "$@" ;;
  status)    do_status ;;
  logs)      do_logs "$@" ;;
  ""|-h|--help|help) usage ;;
  *) usage >&2; die "unknown command: $command" ;;
esac
