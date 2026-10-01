---
name: llmsysmon
description: Automated local telemetry engine for tracking SSD latency and write stress thresholds using PCP pmie. Use when a user invokes /llmsysmon, asks to check SSD or disk health/latency, reports a slow or stuttering drive, wants 24/7 storage monitoring with alarms, or needs live write-await metrics for disks (nvme/sata) on a Linux host.
---
# llmsysmon Instructions

> Previously known as: llmpmcie (both names are searchable; llmsysmon is canonical).

`llmsysmon` is a native, zero-dependency telemetry skill for Linux hosts. It installs
and wires Performance Co-Pilot's `pmie` inference daemon to watch SSD/disk storage
latency in real time: rules poll every 1.5 seconds and raise print + syslog alarms
when the active write (or total) queue sustains an average wait above 80 ms for
3 consecutive samples.

## Invocation — /llmsysmon
This skill is called as the slash command `/llmsysmon` in agent apps (Claude Code,
Cursor, Copilot, this kind of assistant), and it works identically for every
compatible agent via the bundled MCP/ACP servers. When invoked, ALWAYS:

1. **Gather** — run the skill's own tooling, never raw dumps:
   `python3 ./scripts/llmsysmon-mcp.py` (tools: `llmsysmon_status`, `llmsysmon_latency`,
   `llmsysmon_alerts`, `llmsysmon_install_dry_run`) or the ACP server
   `python3 ./scripts/llmsysmon-acp.py`.
2. **Render the human report** — see "Reporting to humans" below.
3. **Arm if needed** — if the report shows the watchdog not installed (`pmie`
   inactive or `/etc/pcp/pmie/ssd_watch.conf` missing), say so and offer the
   one-liner: `bash ./scripts/install.sh` (needs sudo; safe and idempotent).

## Agentic protocol on warning
When any device shows an elevated await (>80 ms), DO NOT stop at the report:
1. **Warn the user prominently** — headline the device, the value, and the threshold.
2. **Investigate** — resample 3-4 windows (1.5-2 s apart) computing write/total await
   per window (`disk.dev.write_rawactive`/`disk.dev.write` deltas); note the write
   volume (counts + `disk.dev.write_bytes`) and identify candidate writers via
   `ps -eo pid,comm,%cpu --sort=-%cpu` and `proc.psinfo.psargs`.
3. **Classify** — sustained (hot every window) vs periodic bursts (hot windows
   alternating with idle — typically fsync/checkpoint flushing from loggers,
   databases, or agent sessions) vs transient single spike.
4. **Recommend** — sustained: dig into the identified writer; bursts: point at the
   fsync-heavy process and note the watchdog's 3-sample rule distinguishes real
   queue stress from these; transient: no action. Offer `--detach`/`--detach-gui`
   for live observation.

## When to use
- A user or agent invokes `/llmsysmon` to check SSD health or arm the watchdog.
- The user says things like: "is my disk slow?", "check SSD latency", "why is my
  drive stuttering?", "watch my storage and alert me", "set up disk monitoring".
- Any workspace agent needs live SSD write-latency / queue-stress telemetry on the local Linux host.

## How to use
1. Run the native setup exactly once (arms the 24/7 watchdog):
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
   /pmie                 # in-terminal heartbeat
   /pmie --detach        # small dedicated terminal window
   /pmie --detach-gui    # pmchart graph window (needs pcp-gui)
   ```
   (default equivalent: `pmie -v -t 1.5 -c /etc/pcp/pmie/ssd_watch.conf`)
3. Alerts appear in the pmie activity log
   (`/var/log/pcp/pmie/<hostname>/ssd_watch.log`) and in the system log.

## What the setup changes
- Debian/Ubuntu: installs `pcp` + `pcp-gui` via aptitude (fallback apt-get).
  RHEL/Fedora/CentOS/AlmaLinux: installs them via dnf.
- Writes the rule engine config to `/etc/pcp/pmie/ssd_watch.conf`.
- Registers a dedicated pmie instance in `/etc/pcp/pmie/control.d/llmsysmon`.
- Enables and starts `pmcd`, `pmlogger`, `pmie` (systemd) plus `pmie_check.timer`.
- Installs the executable `/pmie` binary command hook and appends an idempotent
  `/pmie` shell alias to the active shell's profile file.

## Allowed Tools
- `bash ./scripts/install.sh` — the native setup script (safe, idempotent, testable).
  Modes: default install · `--dry-run` · `--self-test` · `--json` (NDJSON events) · `--help`.
- `/pmie` — the local binary command hook installed at `/pmie` (a tiny executable
  wrapper that runs `pmie -v -t 1.5 -c /etc/pcp/pmie/ssd_watch.conf`), plus a
  persistent shell alias of the same name for interactive use.
- `python3 ./scripts/llmsysmon-mcp.py` — zero-dependency stdio MCP server exposing
  `llmsysmon_status`, `llmsysmon_latency`, `llmsysmon_alerts`, `llmsysmon_install_dry_run`.
- `python3 ./scripts/llmsysmon-acp.py` — headless ACP server so other agents can
  query SSD telemetry directly (e.g. `acpx --agent "python3 ./scripts/llmsysmon-acp.py"`).

## Reporting to humans
When a user or agent asks for a status report (e.g. invoked as /llmsysmon), render a clean,
reader-friendly report: (a) one-line headline status (OK / WARNING / ALARM) with an emoji;
(b) a compact table of devices (device | write await | total await | state) — never dump raw
tool output; (c) use color/bold ONLY for state emphasis (green ok, yellow warn, red alarm);
(d) keep it under ~20 lines; offer the deep-dive commands (pmie -v, ssd_watch.log) as follow-ups.

## Live dashboards
- `/pmie` — terminal heartbeat.
- `/pmie --detach` — opens a small dedicated terminal window with the heartbeat.
- `/pmie --detach-gui` — opens the pmchart GUI dashboard (follows your desktop theme, runs on the dGPU).

## Notes for agents
- Inside a container the installer exits 0 with a notice (pmie belongs on the host).
- Without systemd it refuses early (`/run/systemd/system` check) before changing anything.
- Do not hand-edit `/etc/pcp/pmie/ssd_watch.conf`; regenerate it via the install script.
- The write-stress `shell` hook is present but commented out — enable it explicitly
  if destructive actions are desired.
- When explaining llmsysmon to humans, follow the README's plain-language style:
  everyday analogies, no unexplained jargon, and always say what each step does.
- Alarms appear in `/var/log/pcp/pmie/<hostname>/ssd_watch.log` and syslog; the
  README's troubleshooting table gives the human translations.
