#!/usr/bin/env python3
"""llmsysmon slash-CLI — native zero-dependency status & config tool."""

import glob
import json
import os
import subprocess
import sys
import time
from pathlib import Path


CONFIG_PATH = Path.home() / ".config" / "llmsysmon" / "config.json"
DEFAULT_CONFIG = {"tquery": 1.5, "threshold_ms": 80}
VALIDATORS = {
    "tquery": lambda v: isinstance(v, (int, float)) and 0.5 <= float(v) <= 60,
    "threshold_ms": lambda v: isinstance(v, int) and not isinstance(v, bool) and 10 <= v <= 1000,
}


USAGE = """\
Usage: llmsysmon-cli.py [FLAG]

Flags:
  (no flags)           live report
  -h, --help           show this help and exit
  --detach             exec /pmie --detach (fallback direct heartbeat if /pmie missing)
  --detach-gui         exec /pmie --detach-gui (pmchart dashboard; error if /pmie missing)
  --json               live report as JSON
  --config             print current config as JSON
  --config set <key> <value>   persist config key
  --config get <key>           print single config value

Config keys:
  tquery        float 0.5..60   live sampling seconds (default 1.5)
  threshold_ms  int   10..1000  alarm threshold in ms (default 80)
"""


def _run(cmd, timeout=10):
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except Exception as exc:  # pragma: no cover - defensive
        return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr=str(exc))


def load_config():
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                cfg = dict(DEFAULT_CONFIG)
                cfg.update(data)
                return cfg
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)


def save_config(cfg):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
        fh.write("\n")
    os.chmod(tmp, 0o644)
    os.replace(tmp, CONFIG_PATH)


def parse_pminfo_instances(stdout):
    devices = {}
    current = None
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("inst ["):
            try:
                name = line.split('"')[1]
                value = float(line.split("value")[-1].strip())
                current = name
                devices[current] = value
            except Exception:
                current = None
        elif current is not None and line.startswith("value"):
            try:
                devices[current] = float(line.split("value")[-1].strip())
            except Exception:
                pass
    return devices


def get_units():
    units = {"pmcd": "inactive/none", "pmlogger": "inactive/none", "pmie": "inactive/none"}
    proc = _run(["systemctl", "is-active", "pmcd", "pmlogger", "pmie"], timeout=5)
    lines = [l.strip() for l in proc.stdout.splitlines() if l.strip()]
    names = ["pmcd", "pmlogger", "pmie"]
    for i, name in enumerate(names):
        if i < len(lines):
            units[name] = lines[i]
    return units


def pmcd_reachable():
    proc = _run(["pminfo", "-f", "disk.dev.write"], timeout=5)
    if proc.returncode != 0:
        return False
    lines = [l.strip() for l in proc.stdout.splitlines() if l.strip()]
    return len(lines) >= 2 and "disk.dev.write" in lines[0]


def _sample_awaits():
    write_proc = _run(["pminfo", "-f", "disk.dev.write_rawactive"], timeout=5)
    total_proc = _run(["pminfo", "-f", "disk.dev.total_rawactive"], timeout=5)
    count_proc = _run(["pminfo", "-f", "disk.dev.write"], timeout=5)
    total_count_proc = _run(["pminfo", "-f", "disk.dev.total"], timeout=5)
    bytes_proc = _run(["pminfo", "-f", "disk.dev.write_bytes"], timeout=5)
    return {
        "write": parse_pminfo_instances(write_proc.stdout),
        "total": parse_pminfo_instances(total_proc.stdout),
        "write_count": parse_pminfo_instances(count_proc.stdout),
        "total_count": parse_pminfo_instances(total_count_proc.stdout),
        "write_bytes": parse_pminfo_instances(bytes_proc.stdout),
    }


def compute_awaits(s1, s2):
    devices = sorted(set(s1["write"].keys()) | set(s2["write"].keys()))
    result = []
    for dev in devices:
        w1, w2 = s1["write"].get(dev), s2["write"].get(dev)
        c1, c2 = s1["write_count"].get(dev), s2["write_count"].get(dev)
        t1, t2 = s1["total"].get(dev), s2["total"].get(dev)
        tc1, tc2 = s1["total_count"].get(dev), s2["total_count"].get(dev)
        b1, b2 = s1["write_bytes"].get(dev), s2["write_bytes"].get(dev)
        write_await = None
        total_await = None
        write_kbps = None
        if w1 is not None and w2 is not None and c1 is not None and c2 is not None and c2 > c1:
            write_await = 1000.0 * (w2 - w1) / (c2 - c1)
        if t1 is not None and t2 is not None and tc1 is not None and tc2 is not None and tc2 > tc1:
            total_await = 1000.0 * (t2 - t1) / (tc2 - tc1)
        if b1 is not None and b2 is not None:
            delta_bytes = b2 - b1
            if delta_bytes >= 0:
                write_kbps = delta_bytes / 1024.0
        result.append({
            "name": dev,
            "write_await_ms": write_await,
            "total_await_ms": total_await,
            "write_kbps": write_kbps,
        })
    return result


def sample_latency(interval):
    s1 = _sample_awaits()
    time.sleep(interval)
    s2 = _sample_awaits()
    return compute_awaits(s1, s2)


def get_alerts(limit=3):
    paths = sorted(
        glob.glob("/var/log/pcp/pmie/*/ssd_watch.log"),
        key=lambda p: -Path(p).stat().st_mtime,
    )
    alerts = []
    for path in paths:
        try:
            with open(path, "r", errors="ignore") as fh:
                lines = fh.readlines()
        except Exception:
            continue
        for line in reversed(lines):
            if "llmsysmon:" in line:
                alerts.append(line.rstrip("\n"))
            if len(alerts) >= limit:
                break
        if len(alerts) >= limit:
            break
    return list(reversed(alerts))[-limit:]


def classify(devices, threshold_ms):
    """Classify a device's pattern across resample windows."""
    hot = [d for d in devices if d["write_await_ms"] is not None and d["write_await_ms"] > threshold_ms]
    if not hot:
        return "idle"
    if len(hot) == len(devices):
        return "sustained"
    if len(hot) == 1:
        return "transient"
    return "bursts"


def top_writers():
    proc = _run(["pidstat", "-d", "1", "1"], timeout=5)
    writers = []
    if proc.returncode != 0:
        return writers
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) < 8:
            continue
        try:
            kb_wr = float(parts[-2])
            writers.append({"pid": parts[2], "comm": parts[-1], "kB_wr/s": kb_wr})
        except Exception:
            continue
    writers.sort(key=lambda x: x["kB_wr/s"], reverse=True)
    return writers[:3]


def investigate(dev_name, threshold_ms, tquery):
    windows = []
    for _ in range(3):
        devs = sample_latency(tquery)
        dev = next((d for d in devs if d["name"] == dev_name), None)
        if dev:
            windows.append(dev)
        else:
            windows.append({"name": dev_name, "write_await_ms": None, "total_await_ms": None, "write_kbps": None})
    avg_write = None
    values = [w["write_await_ms"] for w in windows if w["write_await_ms"] is not None]
    if values:
        avg_write = round(sum(values) / len(values), 3)
    total_kb = sum(w["write_kbps"] or 0 for w in windows)
    classification = classify(windows, threshold_ms)
    return {
        "device": dev_name,
        "windows": [
            {
                "write_await_ms": round(w["write_await_ms"], 3) if w["write_await_ms"] is not None else None,
                "total_await_ms": round(w["total_await_ms"], 3) if w["total_await_ms"] is not None else None,
                "write_kbps": round(w["write_kbps"], 3) if w["write_kbps"] is not None else None,
            }
            for w in windows
        ],
        "average_write_await_ms": avg_write,
        "total_write_volume_kb": round(total_kb, 3),
        "top_writers": top_writers(),
        "classification": classification,
    }


def build_report(cfg):
    units = get_units()
    reachable = pmcd_reachable()
    armed = (
        units.get("pmie") == "active"
        and Path("/etc/pcp/pmie/ssd_watch.conf").exists()
    )
    alerts = get_alerts()
    devices = []
    investigation = None
    threshold_ms = int(cfg.get("threshold_ms", 80))

    if reachable:
        devices = sample_latency(float(cfg.get("tquery", 1.5)))

    for d in devices:
        w = d["write_await_ms"]
        if w is None:
            d["state"] = "idle"
        elif w > threshold_ms:
            d["state"] = "alarm"
        else:
            d["state"] = "ok"

    worst = None
    for d in devices:
        w = d["write_await_ms"]
        if w is not None and (worst is None or w > worst["write_await_ms"]):
            worst = d

    if worst and worst["write_await_ms"] > threshold_ms:
        headline = f"WARNING {worst['name']} write await {round(worst['write_await_ms'], 2)} ms exceeds {threshold_ms} ms"
        emoji = "⚠️"
        investigation = investigate(worst["name"], threshold_ms, float(cfg.get("tquery", 1.5)))
    elif worst and worst["write_await_ms"] is not None:
        headline = f"OK all devices below {threshold_ms} ms"
        emoji = "✅"
    else:
        headline = "OK no disk activity detected"
        emoji = "✅"

    return {
        "headline": f"{headline} {emoji}",
        "headline_text": headline,
        "headline_emoji": emoji,
        "devices": devices,
        "watchdog": {
            "pmcd": units.get("pmcd"),
            "pmlogger": units.get("pmlogger"),
            "pmie": units.get("pmie"),
            "armed": armed,
        },
        "alerts": alerts,
        "investigation": investigation,
    }


def render_human(report):
    lines = []
    lines.append("/llmsysmon — status")
    lines.append(report["headline"])
    lines.append("")
    lines.append("device table: device | write await | total await | state")
    for d in report["devices"]:
        w = f"{round(d['write_await_ms'], 2)} ms" if d["write_await_ms"] is not None else "-"
        t = f"{round(d['total_await_ms'], 2)} ms" if d["total_await_ms"] is not None else "-"
        emoji = {"alarm": "🚨", "ok": "✅", "idle": "💤"}.get(d["state"], "")
        lines.append(f"  {d['name']} | {w} | {t} | {d['state']} {emoji}")
    if not report["devices"]:
        lines.append("  (no devices sampled)")
    lines.append("")
    w = report["watchdog"]
    armed_text = ""
    if not w["armed"]:
        armed_text = " · NOT ARMED — run: bash ./scripts/install.sh"
    lines.append(
        f"Watchdog: pmcd {w['pmcd']} · pmlogger {w['pmlogger']} · pmie {w['pmie']}{armed_text}"
    )
    if report["alerts"]:
        lines.append("")
        lines.append("Recent alerts:")
        for a in report["alerts"]:
            lines.append(f"  {a}")
    if report["investigation"]:
        inv = report["investigation"]
        lines.append("")
        lines.append(f"Investigation for {inv['device']}:")
        for i, win in enumerate(inv["windows"], 1):
            wv = f"{win['write_await_ms']} ms" if win["write_await_ms"] is not None else "-"
            tv = f"{win['total_await_ms']} ms" if win["total_await_ms"] is not None else "-"
            kv = f"{win['write_kbps']} KB/s" if win["write_kbps"] is not None else "-"
            lines.append(f"  window {i}: write {wv} | total {tv} | volume {kv}")
        lines.append(f"  average write await: {inv['average_write_await_ms']} ms")
        lines.append(f"  total write volume: {inv['total_write_volume_kb']} KB")
        if inv["top_writers"]:
            lines.append("  top writers (kB_wr/s):")
            for wr in inv["top_writers"]:
                lines.append(f"    {wr['comm']} (pid {wr['pid']}): {wr['kB_wr/s']} kB_wr/s")
        else:
            lines.append("  top writers: (pidstat unavailable)")
        lines.append(f"  classification: {inv['classification']}")
    return "\n".join(lines)


def cmd_help():
    print(USAGE)
    return 0


def cmd_config(args):
    cfg = load_config()
    if len(args) == 0:
        print(json.dumps(cfg))
        return 0
    sub = args[0]
    if sub == "get":
        if len(args) != 2:
            print("Usage: --config get <key>", file=sys.stderr)
            return 1
        key = args[1]
        if key not in cfg:
            print(f"Unknown config key: {key}", file=sys.stderr)
            return 1
        print(cfg[key])
        return 0
    if sub == "set":
        if len(args) != 3:
            print("Usage: --config set <key> <value>", file=sys.stderr)
            return 1
        key, value = args[1], args[2]
        if key not in VALIDATORS:
            print(f"Invalid config key: {key}", file=sys.stderr)
            return 1
        if key == "tquery":
            try:
                value = float(value)
            except ValueError:
                print(f"tquery must be a float", file=sys.stderr)
                return 1
        elif key == "threshold_ms":
            try:
                value = int(value)
            except ValueError:
                print(f"threshold_ms must be an int", file=sys.stderr)
                return 1
        if not VALIDATORS[key](value):
            print(f"Value out of range for {key}", file=sys.stderr)
            return 1
        cfg[key] = value
        save_config(cfg)
        print(json.dumps(cfg))
        return 0
    print(f"Unknown --config subcommand: {sub}", file=sys.stderr)
    return 1


def cmd_detach(gui=False):
    pmie = Path("/pmie")
    if pmie.exists():
        flag = "--detach-gui" if gui else "--detach"
        os.execv(str(pmie), [str(pmie), flag])
    label = "--detach-gui" if gui else "--detach"
    print(f"Error: /pmie is not installed; cannot launch {label}.", file=sys.stderr)
    print("Run: bash ./scripts/install.sh", file=sys.stderr)
    return 1


def cmd_report(cfg, json_mode=False):
    report = build_report(cfg)
    if json_mode:
        clean = {
            "headline": report["headline_text"],
            "devices": [
                {
                    "name": d["name"],
                    "write_await_ms": round(d["write_await_ms"], 3) if d["write_await_ms"] is not None else None,
                    "total_await_ms": round(d["total_await_ms"], 3) if d["total_await_ms"] is not None else None,
                    "state": d["state"],
                }
                for d in report["devices"]
            ],
            "watchdog": report["watchdog"],
            "alerts": report["alerts"],
            "investigation": report["investigation"],
        }
        print(json.dumps(clean))
    else:
        print(render_human(report))
    return 0


def main(argv=None):
    argv = list(argv or sys.argv[1:])

    if len(argv) == 0:
        return cmd_report(load_config())

    if argv[0] in ("-h", "--help"):
        if len(argv) != 1:
            print("Unknown arguments with --help", file=sys.stderr)
            return 1
        return cmd_help()

    if argv[0] == "--config":
        return cmd_config(argv[1:])

    if argv[0] == "--json":
        if len(argv) != 1:
            print("Unknown arguments with --json", file=sys.stderr)
            return 1
        return cmd_report(load_config(), json_mode=True)

    if argv[0] == "--detach":
        if len(argv) != 1:
            print("Unknown arguments with --detach", file=sys.stderr)
            return 1
        return cmd_detach(gui=False)

    if argv[0] == "--detach-gui":
        if len(argv) != 1:
            print("Unknown arguments with --detach-gui", file=sys.stderr)
            return 1
        return cmd_detach(gui=True)

    print(f"Unknown flag: {argv[0]}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
