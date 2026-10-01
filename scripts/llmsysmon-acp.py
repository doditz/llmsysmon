#!/usr/bin/env python3
"""llmsysmon headless ACP server — stdio JSON-RPC 2.0."""

import glob
import json
import subprocess
import sys
import time
from pathlib import Path


def _run(cmd, timeout=10):
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except Exception as exc:  # pragma: no cover - defensive
        return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr=str(exc))


def _install_script() -> Path:
    return Path(__file__).resolve().parent / "install.sh"


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


def get_devices():
    proc = _run(["pminfo", "-f", "disk.dev.write"], timeout=5)
    if proc.returncode != 0:
        return []
    return sorted(parse_pminfo_instances(proc.stdout).keys())


def _sample_awaits():
    write_proc = _run(["pminfo", "-f", "disk.dev.write_rawactive"], timeout=5)
    total_proc = _run(["pminfo", "-f", "disk.dev.total_rawactive"], timeout=5)
    count_proc = _run(["pminfo", "-f", "disk.dev.write"], timeout=5)
    total_count_proc = _run(["pminfo", "-f", "disk.dev.total"], timeout=5)
    return {
        "write": parse_pminfo_instances(write_proc.stdout),
        "total": parse_pminfo_instances(total_proc.stdout),
        "write_count": parse_pminfo_instances(count_proc.stdout),
        "total_count": parse_pminfo_instances(total_count_proc.stdout),
    }


def tool_latency(interval=1.5):
    s1 = _sample_awaits()
    time.sleep(interval)
    s2 = _sample_awaits()
    devices = sorted(set(s1["write"].keys()) | set(s2["write"].keys()))
    result = []
    for dev in devices:
        w1, w2 = s1["write"].get(dev), s2["write"].get(dev)
        c1, c2 = s1["write_count"].get(dev), s2["write_count"].get(dev)
        t1, t2 = s1["total"].get(dev), s2["total"].get(dev)
        tc1, tc2 = s1["total_count"].get(dev), s2["total_count"].get(dev)
        write_await = None
        total_await = None
        if w1 is not None and w2 is not None and c1 is not None and c2 is not None and c2 > c1:
            write_await = round(1000.0 * (w2 - w1) / (c2 - c1), 3)
        if t1 is not None and t2 is not None and tc1 is not None and tc2 is not None and tc2 > tc1:
            total_await = round(1000.0 * (t2 - t1) / (tc2 - tc1), 3)
        result.append({
            "name": dev,
            "write_await_ms": write_await,
            "total_await_ms": total_await,
        })
    return {"interval_s": interval, "devices": result}


def tool_alerts():
    paths = sorted(glob.glob("/var/log/pcp/pmie/*/ssd_watch.log"), key=lambda p: -Path(p).stat().st_mtime)
    alerts = []
    for path in paths:
        try:
            with open(path, "r", errors="ignore") as fh:
                lines = fh.readlines()
        except Exception:
            continue
        for line in lines:
            if "llmsysmon:" in line:
                alerts.append(line.rstrip("\n"))
        if len(alerts) >= 10:
            break
    return alerts[:10]


def tool_install_dry_run():
    script = _install_script()
    proc = _run(["bash", str(script), "--json", "--dry-run"], timeout=60)
    events = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except Exception:
            pass
    return events


def build_status_text():
    units = get_units()
    reachable = pmcd_reachable()
    devices = get_devices() if reachable else []
    latency = tool_latency() if reachable else {"interval_s": 1.5, "devices": []}
    lines = [
        "llmsysmon telemetry status",
        f"pmcd: {units['pmcd']}",
        f"pmlogger: {units['pmlogger']}",
        f"pmie: {units['pmie']}",
        f"pmcd reachable: {reachable}",
        f"devices: {', '.join(devices) if devices else 'none'}",
    ]
    if latency["devices"]:
        lines.append("latency snapshot:")
        for d in latency["devices"]:
            wa = d["write_await_ms"] if d["write_await_ms"] is not None else "n/a"
            ta = d["total_await_ms"] if d["total_await_ms"] is not None else "n/a"
            lines.append(f"  {d['name']}: write_await={wa} ms, total_await={ta} ms")
    else:
        lines.append("latency snapshot: unavailable (pmcd unreachable or no devices)")
    return "\n".join(lines)


def build_latency_text():
    latency = tool_latency()
    lines = [f"llmsysmon SSD latency snapshot (interval {latency['interval_s']}s)"]
    if not latency["devices"]:
        lines.append("No device data available (pmcd may be unreachable).")
        return "\n".join(lines)
    for d in latency["devices"]:
        wa = d["write_await_ms"] if d["write_await_ms"] is not None else "n/a"
        ta = d["total_await_ms"] if d["total_await_ms"] is not None else "n/a"
        lines.append(f"{d['name']:<14} write {wa:>8} ms   total {ta:>8} ms")
    return "\n".join(lines)


def build_alerts_text():
    alerts = tool_alerts()
    if not alerts:
        return "No recent llmsysmon alerts."
    return "Recent llmsysmon alerts:\n" + "\n".join(alerts)


def build_install_text():
    events = tool_install_dry_run()
    lines = ["llmsysmon install dry-run summary:"]
    for evt in events:
        lines.append(json.dumps(evt))
    if not events:
        lines.append("(no events returned)")
    return "\n".join(lines)


def answer_prompt(text):
    lowered = text.lower()
    if "install" in lowered or "dry-run" in lowered or "dry run" in lowered:
        return build_install_text()
    if "alert" in lowered:
        return build_alerts_text()
    if "latency" in lowered:
        return build_latency_text()
    return build_status_text()


class MethodNotFound(Exception):
    pass


def handle_request(req):
    method = req.get("method")
    if method == "initialize":
        return {
            "protocolVersion": 1,
            "agentCapabilities": {
                "loadSession": False,
                "promptCapabilities": {"image": False, "audio": False, "embeddedContext": False},
            },
            "authMethods": [],
        }
    if method == "session/new":
        return {"sessionId": "llmsysmon-session"}
    if method == "session/prompt":
        params = req.get("params", {})
        prompt = params.get("prompt", [])
        text = ""
        for part in prompt:
            if isinstance(part, dict) and part.get("type") == "text":
                text = part.get("text", "")
                break
        answer = answer_prompt(text)
        return {
            "stopReason": "end_turn",
            "message": {"role": "assistant", "content": [{"type": "text", "text": answer}]},
        }
    if method == "session/cancel":
        return {"stopReason": "cancelled"}
    if method == "session/set_mode":
        return {}
    raise MethodNotFound(method)


def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue
        req_id = req.get("id")
        try:
            result = handle_request(req)
        except MethodNotFound:
            if req_id is not None:
                send({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": "Method not found"}})
            continue
        if req_id is not None:
            send({"jsonrpc": "2.0", "id": req_id, "result": result})


if __name__ == "__main__":
    main()
