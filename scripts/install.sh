#!/usr/bin/env bash
# llmsysmon — native SSD telemetry installer
# Usage: scripts/install.sh [--self-test|--dry-run]

set -euo pipefail

# --- ANSI tracing helpers -----------------------------------------------------
C_R=''; C_G=''; C_Y=''; C_B=''; C_N=''
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  C_R='\033[0;31m'
  C_G='\033[0;32m'
  C_Y='\033[1;33m'
  C_B='\033[1;34m'
  C_N='\033[0m'
fi

say()  { printf "${C_B}[llmsysmon]${C_N} %s\n" "$*"; }
ok()   { printf "${C_G}[ok]${C_N}        %s\n" "$*"; }
warn() { printf "${C_Y}[warn]${C_N}      %s\n" "$*" >&2; }
die()  { printf "${C_R}[error]${C_N}     %s\n" "$*" >&2; exit 1; }

# --- pure functions -----------------------------------------------------------
detect_shell_rc() {  # $1: shell path -> profile rc file path
  case "$(basename "${1:-/bin/bash}")" in
    zsh) printf '%s/.zshrc\n' "$HOME" ;;
    *)   printf '%s/.bashrc\n' "$HOME" ;;
  esac
}

detect_os_family() {  # prints debian|rhel; non-zero on unknown/unreadable
  local os_release="${LLMPMCIE_OS_RELEASE:-/etc/os-release}" id id_like
  [ -r "$os_release" ] || return 2
  id=$(sed -nE 's/^ID=//p' "$os_release" | head -n1 | tr -d '"' | tr '[:upper:]' '[:lower:]')
  id_like=$(sed -nE 's/^ID_LIKE=//p' "$os_release" | head -n1 | tr -d '"' | tr '[:upper:]' '[:lower:]')
  case "$id $id_like" in
    *debian*|*ubuntu*) echo debian; return 0 ;;
    *rhel*|*fedora*|*centos*|*almalinux*|*rocky*) echo rhel; return 0 ;;
    *) return 1 ;;
  esac
}

in_container() {  # inverted isolation-boundary check
  grep -qE '(docker|kubepods|containerd|podman|libpod|lxc)' /proc/1/cgroup 2>/dev/null
}

gen_alias_line() {
  printf "%s" "alias /pmie='pmie -v -t 1.5 -c /etc/pcp/pmie/ssd_watch.conf'"
}

alias_present() { grep -qF 'alias /pmie=' "$1" 2>/dev/null; }

# --- self-test ----------------------------------------------------------------
run_selftest() {
  local fails=0 tmp
  pass() { echo "PASS: $*"; }
  fail() { echo "FAIL: $*"; fails=$((fails+1)); }
  assert_eq() { if [ "$1" = "$2" ]; then pass "$3"; else fail "$3 (got '$1', want '$2')"; fi; }
  assert_fails() { if "$@" >/dev/null 2>&1; then fail "expected failure: $*"; else pass "expected failure: $*"; fi; }

  tmp="$(mktemp -d)" || exit 1

  # shell profile detection
  assert_eq "$(detect_shell_rc /bin/bash)" "$HOME/.bashrc" "bash -> ~/.bashrc"
  assert_eq "$(detect_shell_rc /usr/bin/zsh)" "$HOME/.zshrc" "zsh -> ~/.zshrc"
  assert_eq "$(detect_shell_rc '')" "$HOME/.bashrc" "empty shell -> ~/.bashrc fallback"

  # OS distribution matrix (via env-overridden os-release)
  for id in debian ubuntu; do
    printf 'ID=%s\n' "$id" > "$tmp/rel"
    assert_eq "$(LLMPMCIE_OS_RELEASE="$tmp/rel" detect_os_family)" debian "$id -> debian family"
  done
  for id in rhel fedora centos almalinux rocky; do
    printf 'ID=%s\n' "$id" > "$tmp/rel"
    assert_eq "$(LLMPMCIE_OS_RELEASE="$tmp/rel" detect_os_family)" rhel "$id -> rhel family"
  done
  printf 'ID=ubuntu\nID_LIKE=debian\n' > "$tmp/rel"
  assert_eq "$(LLMPMCIE_OS_RELEASE="$tmp/rel" detect_os_family)" debian "ID_LIKE fallback -> debian"
  printf 'ID=arch\n' > "$tmp/rel"
  assert_fails env LLMPMCIE_OS_RELEASE="$tmp/rel" detect_os_family
  assert_fails env LLMPMCIE_OS_RELEASE="$tmp/nonexistent" detect_os_family

  # alias generation + idempotency guard
  assert_eq "$(gen_alias_line)" \
    "alias /pmie='pmie -v -t 1.5 -c /etc/pcp/pmie/ssd_watch.conf'" "alias line exact"
  printf '%s\n' "$(gen_alias_line)" > "$tmp/rc"
  if alias_present "$tmp/rc"; then pass "alias_present detects existing alias"; else fail "alias_present misses existing alias"; fi
  : > "$tmp/rc"
  if alias_present "$tmp/rc"; then fail "alias_present false positive on empty file"; else pass "alias_present ignores empty file"; fi

  rm -rf "$tmp"
  if [ "$fails" -eq 0 ]; then echo "SELF-TEST: all PASS"; exit 0; else echo "SELF-TEST: $fails FAIL"; exit 1; fi
}

# --- lifecycle globals --------------------------------------------------------
SSD_WATCH_CONF='/etc/pcp/pmie/ssd_watch.conf'
PMIE_CONTROL_D='/etc/pcp/pmie/control.d'
DRY_RUN=0
if [ "${1:-}" = "--dry-run" ] || [ "${LLMPMCIE_DRY_RUN:-0}" = 1 ]; then DRY_RUN=1; fi

run() {  # every lifecycle action is traced; dry-run prints instead of executing
  if [ "$DRY_RUN" = 1 ]; then printf '[dry-run] %s\n' "$*"; return 0; fi
  "$@"
}

install_packages() {
  local family="$1" sudo_bin="$2"
  case "$family" in
    debian)
      if ! dpkg -s pcp >/dev/null 2>&1 || ! dpkg -s pcp-gui >/dev/null 2>&1; then
        say "inverted dpkg check: pcp/pcp-gui missing — installing"
        if [ "$DRY_RUN" = 1 ]; then
          printf '[dry-run] %s update && %s install pcp pcp-gui\n' "$sudo_bin" "$sudo_bin"
          return 0
        fi
        if command -v aptitude >/dev/null 2>&1; then
          say "aptitude found — using aptitude"
          run $sudo_bin aptitude update || die "aptitude update failed"
          run $sudo_bin aptitude -y install pcp pcp-gui || die "aptitude install failed"
        else
          say "aptitude absent — falling back to apt-get"
          run $sudo_bin apt-get update || die "apt-get update failed"
          run $sudo_bin apt-get -y install pcp pcp-gui || die "apt-get install failed"
        fi
        ok "pcp and pcp-gui installed"
      else
        ok "pcp and pcp-gui already present (dpkg)"
      fi
      ;;
    rhel)
      if ! rpm -q pcp >/dev/null 2>&1 || ! rpm -q pcp-gui >/dev/null 2>&1; then
        say "inverted rpm check: pcp/pcp-gui missing — installing via dnf"
        if [ "$DRY_RUN" = 1 ]; then
          printf '[dry-run] %s dnf -y install pcp pcp-gui\n' "$sudo_bin"
          return 0
        fi
        run $sudo_bin dnf -y install pcp || die "dnf install pcp failed"
        run $sudo_bin dnf -y install pcp-gui || warn "pcp-gui unavailable here — continuing without GUI"
        ok "pcp installed (pcp-gui best-effort)"
      else
        ok "pcp and pcp-gui already present (rpm)"
      fi
      ;;
  esac
}

# --- main installer -----------------------------------------------------------
main() {
  say "llmsysmon — native SSD telemetry installer"
  if [ "${1:-}" = "--self-test" ]; then run_selftest; fi

  # 1) container isolation boundary (inverted: exit cleanly when inside)
  if in_container; then
    say "Container boundary detected in /proc/1/cgroup — pmie belongs on the host; skipping install."
    exit 0
  fi
  ok "not inside a container isolation boundary — proceeding"

  # 2) OS distribution matrix
  local family
  if ! family="$(detect_os_family)"; then
    die "unsupported OS: cannot match Debian/Ubuntu or RHEL/Fedora/CentOS/AlmaLinux/Rocky in /etc/os-release"
  fi
  say "OS family detected: $family"

  # 3) active shell profile target
  local rc_file
  rc_file="$(detect_shell_rc "${SHELL:-/bin/bash}")"
  if [ "$(id -u)" -eq 0 ] && [ -n "${SUDO_USER:-}" ]; then
    local sudohome
    sudohome="$(getent passwd "$SUDO_USER" 2>/dev/null | cut -d: -f6 || true)"
    [ -n "$sudohome" ] && rc_file="$sudohome/$(basename "$rc_file")"
  fi
  say "shell profile target: $rc_file"

  # 4) elevation strategy
  local SUDO=''
  if [ "$(id -u)" -ne 0 ]; then
    command -v sudo >/dev/null 2>&1 || die "sudo is required for elevated steps"
    SUDO='sudo'
  fi
  ok "elevation: ${SUDO:+sudo (prompts when needed)}${SUDO:-root}"

  # 5) dependency layer
  install_packages "$family" "$SUDO"

  # 6) telemetry rule injection
  say "injecting pmie rules -> $SSD_WATCH_CONF"
  if [ "$DRY_RUN" = 1 ]; then
    printf '[dry-run] write %s (1.5 s polling, >80 ms x 3 samples)\n' "$SSD_WATCH_CONF"
  else
    $SUDO tee "$SSD_WATCH_CONF" >/dev/null <<'LLMPMCIE_RULES'
// llmsysmon — SSD write-latency watchdog (managed by scripts/install.sh)
// Polls every 1.5 s. Alarms when the active write (or total) queue sustains an
// average service+wait time above 80 ms for 3 consecutive samples.
// Math: delta(write_rawactive[ms]) / delta(write[count]) = average wait [ms].
delta = 1.5 sec;

some_inst (
    all_sample ( disk.dev.write_rawactive @0..2 / disk.dev.write @0..2 > 80 msec )
) -> print "llmsysmon: SSD WRITE latency >80ms sustained on %i" &
     syslog "llmsysmon: SSD write latency >80ms sustained on %i";

some_inst (
    all_sample ( disk.dev.total_rawactive @0..2 / disk.dev.total @0..2 > 80 msec )
) -> print "llmsysmon: SSD TOTAL queue latency >80ms sustained on %i" &
     syslog "llmsysmon: SSD total queue latency >80ms sustained on %i";

// Active write-stress hook (disabled by default — uncomment to enable):
// some_inst (
//     all_sample ( disk.dev.write_rawactive @0..2 / disk.dev.write @0..2 > 80 msec )
// ) -> shell 10 min "logger -t llmsysmon 'write-stress hook fired on %i'";
LLMPMCIE_RULES
    $SUDO chmod 0644 "$SSD_WATCH_CONF"
    ok "rules written to $SSD_WATCH_CONF"
  fi

  # 7) syntax validation with the real engine
  say "validating rules with pmie -C"
  if [ "$DRY_RUN" = 1 ]; then
    printf '[dry-run] pmie -C -c %s\n' "$SSD_WATCH_CONF"
  elif command -v pmie >/dev/null 2>&1; then
    if pmie -C -c "$SSD_WATCH_CONF" >/dev/null 2>&1; then
      ok "pmie accepted the rule block"
    else
      die "pmie rejected $SSD_WATCH_CONF — inspect with: pmie -C -c $SSD_WATCH_CONF"
    fi
  else
    warn "pmie binary missing — skipping syntax validation"
  fi

  # 8) dedicated pmie instance registration
  say "registering dedicated pmie instance"
  if [ "$DRY_RUN" = 1 ]; then
    printf '[dry-run] mkdir -p %s; write %s/llmsysmon\n' "$PMIE_CONTROL_D" "$PMIE_CONTROL_D"
  else
    $SUDO mkdir -p "$PMIE_CONTROL_D"
    $SUDO tee "$PMIE_CONTROL_D/llmsysmon" >/dev/null <<'LLMPMCIE_CONTROL'
$version=1.1

# llmsysmon SSD latency watchdog — managed by llmsysmon scripts/install.sh
#Host          P?  S?  Log File                                        Arguments
LOCALHOSTNAME   n   n   PCP_LOG_DIR/pmie/LOCALHOSTNAME/ssd_watch.log   -c /etc/pcp/pmie/ssd_watch.conf
LLMPMCIE_CONTROL
    $SUDO chmod 0644 "$PMIE_CONTROL_D/llmsysmon"
    ok "instance registered in $PMIE_CONTROL_D/llmsysmon"
  fi

  # 9) systemd wiring: pmcd, pmlogger, pmie + instance watchdog timer
  for svc in pmcd pmlogger pmie; do
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
      say "$svc already active — ensuring enabled"
      run $SUDO systemctl enable "$svc" >/dev/null 2>&1 || warn "$svc enable failed"
    else
      say "enabling and starting $svc"
      run $SUDO systemctl enable --now "$svc" || die "$svc failed to start"
    fi
    ok "$svc enabled and active"
  done
  if systemctl list-unit-files --type=timer 2>/dev/null | grep -q '^pmie_check\.timer'; then
    say "enabling pmie_check.timer (restarts the ssd_watch instance)"
    run $SUDO systemctl enable --now pmie_check.timer || warn "pmie_check.timer enable failed"
  else
    warn "pmie_check.timer unit missing — instance restarts rely on system rc"
  fi

  # 10) start the instance now
  local pmie_check_bin
  pmie_check_bin="$(command -v pmie_check 2>/dev/null || true)"
  [ -n "$pmie_check_bin" ] || pmie_check_bin='/usr/lib/pcp/bin/pmie_check'
  if [ "$DRY_RUN" = 1 ]; then
    printf '[dry-run] %s pmie_check (starts ssd_watch instance)\n' "$SUDO"
  elif [ -x "$pmie_check_bin" ]; then
    say "starting pmie instances via pmie_check"
    run $SUDO "$pmie_check_bin" || warn "pmie_check exited non-zero (see /var/log/pcp/pmie/pmie_check.log)"
    sleep 2
    if pgrep -f 'ssd_watch.conf' >/dev/null 2>&1; then
      ok "ssd_watch pmie instance is running"
    else
      warn "ssd_watch instance not seen yet — pmie_check.timer will retry (pgrep -af ssd_watch)"
    fi
  else
    warn "pmie_check not found — reinstall the pcp package"
  fi

  # 11) install the /pmie binary command hook (path command, works in any shell)
  say "installing /pmie binary command hook"
  if [ "$DRY_RUN" = 1 ]; then
    printf '[dry-run] write /pmie wrapper (exec pmie -v -t 1.5 -c %s)\n' "$SSD_WATCH_CONF"
  else
    $SUDO tee /pmie >/dev/null <<'LLMPMCIE_HOOK'
#!/bin/sh
# llmsysmon — instant SSD latency trace (managed by scripts/install.sh)
exec pmie -v -t 1.5 -c /etc/pcp/pmie/ssd_watch.conf
LLMPMCIE_HOOK
    $SUDO chmod 0755 /pmie
    ok "/pmie binary hook installed"
  fi

  # 12) persistent /pmie alias in the detected shell profile
  if alias_present "$rc_file"; then
    ok "alias /pmie already present in $rc_file"
  else
    say "appending /pmie alias to $rc_file"
    if [ "$DRY_RUN" = 1 ]; then
      printf '[dry-run] append to %s: %s\n' "$rc_file" "$(gen_alias_line)"
    else
      {
        printf '\n# llmsysmon — instant SSD latency trace (managed by scripts/install.sh)\n'
        printf '%s\n' "$(gen_alias_line)"
      } >> "$rc_file" || die "could not write $rc_file"
      ok "alias installed — reload with: source $rc_file"
    fi
  fi

  # 13) summary
  say "------------------------------------------------------------"
  say "llmsysmon setup complete"
  say "  live trace: /pmie   (pmie -v -t 1.5 -c $SSD_WATCH_CONF)"
  say "  rules:      $SSD_WATCH_CONF"
  say "  alerts:     /var/log/pcp/pmie/$(hostname)/ssd_watch.log"
  [ "$DRY_RUN" = 1 ] && say "dry-run: nothing was changed"
}

main "${1:-}"
