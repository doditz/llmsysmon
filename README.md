# llmsysmon

Native SSD latency telemetry for Linux — PCP `pmie` rules that poll every 1.5 s
and alarm when write (or total) queue latency sustains above 80 ms for 3 samples,
plus a `/pmie` binary command hook for instant live traces.

> Previously known as: llmpmcie (both names are searchable; llmsysmon is canonical).

## Install

```bash
npx skills add doditz/llmsysmon
```

Then run the setup once:

```bash
bash scripts/install.sh            # verbose + idempotent
bash scripts/install.sh --dry-run  # preview only
bash scripts/install.sh --self-test  # unit self-tests, no system changes
```

Manual install (no skills CLI):

```bash
git clone https://github.com/doditz/llmsysmon.git
bash llmsysmon/scripts/install.sh
```

## Use

```bash
/pmie   # live latency trace every 1.5 s
```

Alerts land in `/var/log/pcp/pmie/<hostname>/ssd_watch.log` and syslog.

## Requirements

- Linux with systemd and sudo (Debian/Ubuntu or RHEL/Fedora/CentOS/AlmaLinux)
- Not inside a container — the installer exits cleanly with a notice if it is
