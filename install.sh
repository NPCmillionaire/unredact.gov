#!/usr/bin/env bash
# unredact.me installer
#
#   curl -fsSL https://raw.githubusercontent.com/NPCmillionaire/unredact.me/main/install.sh | bash
#
# Options (pass after `bash -s --` when piping, e.g. `| bash -s -- --web-forms`):
#   --web-forms   also install Playwright + Chromium (auto-fill congressional contact forms)
#   --no-init     skip the interactive setup wizard
#   --local       install from the current checkout instead of cloning
#   --uninstall   remove unredact (keeps your config and data)
#
# Env overrides: UNREDACT_HOME (install dir), UNREDACT_BIN (symlink dir), UNREDACT_REF (git branch/tag)
set -euo pipefail

REPO="${UNREDACT_REPO:-https://github.com/NPCmillionaire/unredact.me.git}"
REF="${UNREDACT_REF:-main}"
HOME_DIR="${UNREDACT_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/unredact}"
BIN_DIR="${UNREDACT_BIN:-$HOME/.local/bin}"
WEBFORMS=0 INIT=1 LOCAL=0 UNINSTALL=0

for arg in "$@"; do
  case "$arg" in
    --web-forms) WEBFORMS=1 ;;
    --no-init)   INIT=0 ;;
    --local)     LOCAL=1 ;;
    --uninstall) UNINSTALL=1 ;;
    -h|--help)   sed -n '2,15p' "$0" 2>/dev/null || true; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

if [ -t 1 ]; then B=$'\e[1m'; R=$'\e[31m'; G=$'\e[32m'; Y=$'\e[33m'; D=$'\e[2m'; N=$'\e[0m'; else B= R= G= Y= D= N=; fi
say()  { printf '%s\n' "${B}▸${N} $*"; }
ok()   { printf '%s\n' "${G}✓${N} $*"; }
warn() { printf '%s\n' "${Y}!${N} $*"; }
die()  { printf '%s\n' "${R}✗ $*${N}" >&2; exit 1; }

if [ "$UNINSTALL" = 1 ]; then
  rm -rf "$HOME_DIR/venv" "$HOME_DIR/src"
  rm -f "$BIN_DIR/unredact"
  ok "uninstalled (config in ~/.config/unredact and data in ~/.local/share/unredact were kept)"
  exit 0
fi

printf '\n%s\n%s\n\n' "${B}unredact.me${N} — make the government write back" "${D}installing to $HOME_DIR${N}"

# ── prerequisites ──────────────────────────────────────────────────────────
command -v git >/dev/null || die "git is required (macOS: xcode-select --install · Arch: pacman -S git · Debian: apt install git)"

PY=""
for cand in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$cand" >/dev/null && "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    PY="$(command -v "$cand")"; break
  fi
done
[ -n "$PY" ] || die "Python 3.10+ is required (macOS: brew install python · Arch: pacman -S python · Debian: apt install python3 python3-venv)"
"$PY" -c 'import venv, ensurepip' 2>/dev/null || die "Python venv module missing (Debian/Ubuntu: sudo apt install python3-venv)"
ok "using $("$PY" --version) at $PY"

# ── source ─────────────────────────────────────────────────────────────────
mkdir -p "$HOME_DIR"
if [ "$LOCAL" = 1 ]; then
  [ -f pyproject.toml ] && grep -q '^name = "unredact"' pyproject.toml || die "--local must be run from an unredact checkout"
  SRC="$(pwd)"
  ok "installing from local checkout $SRC"
elif [ -d "$HOME_DIR/src/.git" ]; then
  say "updating source"
  git -C "$HOME_DIR/src" fetch --quiet --depth 1 origin "$REF"
  git -C "$HOME_DIR/src" checkout --quiet -B "$REF" FETCH_HEAD
  SRC="$HOME_DIR/src"
else
  say "downloading source"
  git clone --quiet --depth 1 --branch "$REF" "$REPO" "$HOME_DIR/src"
  SRC="$HOME_DIR/src"
fi

# ── virtualenv ─────────────────────────────────────────────────────────────
say "creating virtual environment"
"$PY" -m venv "$HOME_DIR/venv"
VPY="$HOME_DIR/venv/bin/python"
"$VPY" -m pip install --quiet --upgrade pip
EXTRA=""; [ "$WEBFORMS" = 1 ] && EXTRA="[webforms]"
say "installing unredact${EXTRA}"
if [ "$LOCAL" = 1 ]; then
  "$VPY" -m pip install --quiet -e "${SRC}${EXTRA}"
else
  "$VPY" -m pip install --quiet "${SRC}${EXTRA}"
fi
if [ "$WEBFORMS" = 1 ]; then
  say "installing headless Chromium for web forms"
  "$VPY" -m playwright install chromium >/dev/null
fi

mkdir -p "$BIN_DIR"
ln -sf "$HOME_DIR/venv/bin/unredact" "$BIN_DIR/unredact"
ok "installed $("$BIN_DIR/unredact" --help >/dev/null && echo "$BIN_DIR/unredact")"

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *)
    RC="$HOME/.bashrc"; case "${SHELL:-}" in */zsh) RC="$HOME/.zshrc" ;; */fish) RC="$HOME/.config/fish/config.fish" ;; esac
    warn "$BIN_DIR is not on your PATH. Add it:"
    printf '    echo '\''export PATH="%s:$PATH"'\'' >> %s && source %s\n' "$BIN_DIR" "$RC" "$RC"
    ;;
esac

# ── setup wizard ───────────────────────────────────────────────────────────
if [ "$INIT" = 1 ] && [ -r /dev/tty ] && [ -w /dev/tty ]; then
  printf '\n'
  say "running setup (Ctrl-C to skip; rerun any time with: unredact init)"
  "$BIN_DIR/unredact" init </dev/tty || warn "setup skipped — run 'unredact init' later"
else
  printf '\nNext: %s\n' "${B}unredact init${N}"
fi
