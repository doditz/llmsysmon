---
name: llmpmcie
description: Automated local telemetry engine for tracking SSD latency and write stress thresholds using PCP pmie.
---
# llmpmcie Instructions

`llmpmcie` is a native, zero-dependency telemetry skill for Linux hosts. It installs
and wires Performance Co-Pilot's `pmie` inference daemon to watch SSD/disk storage
latency in real time: rules poll every 1.5 seconds and raise print + syslog alarms
when the active write (or total) queue sustains an average wait above 80 ms for
3 consecutive samples.

## When to use
- A workspace agent or operator needs real-time SSD write-latency / queue-stress
  telemetry on the local Linux host.
- You want the persistent `/pmie` binary command hook that prints live latency evaluations on demand.

## How to use
1. Run the native setup exactly once:
   ```bash
   bash ./scripts/install.sh
   ```
   The script is idempotent and verbose: it traces every lifecycle milestone
   (container check, OS matrix, package presence, rule injection, systemd wiring,
   alias install) with colored validation messages.
   - `--dry-run` prints the full planned action trace without changing the system.
   - `--self-test` runs the embedded unit tests (no system changes).
2. After install, evaluate live traces on demand:
   ```bash
   /pmie
   ```
   (equivalent: `pmie -v -t 1.5 -c /etc/pcp/pmie/ssd_watch.conf`)
3. Alerts appear in the pmie activity log
   (`/var/log/pcp/pmie/<hostname>/ssd_watch.log`) and in the system log.

## What the setup changes
- Debian/Ubuntu: installs `pcp` + `pcp-gui` via aptitude (fallback apt-get).
  RHEL/Fedora/CentOS/AlmaLinux: installs them via dnf.
- Writes the rule engine config to `/etc/pcp/pmie/ssd_watch.conf`.
- Registers a dedicated pmie instance in `/etc/pcp/pmie/control.d/llmpmcie`.
- Enables and starts `pmcd`, `pmlogger`, `pmie` (systemd) plus `pmie_check.timer`.
- Installs the executable `/pmie` binary command hook and appends an idempotent
  `/pmie` shell alias to the active shell's profile file.

## Allowed Tools
- `bash ./scripts/install.sh` — the native setup script (safe, idempotent, testable).
- `/pmie` — the local binary command hook installed at `/pmie` (a tiny executable
  wrapper that runs `pmie -v -t 1.5 -c /etc/pcp/pmie/ssd_watch.conf`), plus a
  persistent shell alias of the same name for interactive use.

## Notes for agents
- Inside a container the installer exits 0 with a notice (pmie belongs on the host).
- Do not hand-edit `/etc/pcp/pmie/ssd_watch.conf`; regenerate it via the install script.
- The write-stress `shell` hook is present but commented out — enable it explicitly
  if destructive actions are desired.
