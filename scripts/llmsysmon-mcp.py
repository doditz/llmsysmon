#!/usr/bin/env python3
"""llmsysmon MCP tool server — stdio JSON-RPC 2.0."""

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
        out = subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr=str(exc))
        return out


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
    """Parse pminfo -f output into {instance_name: value}."""
    devices = {}
    current = None
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("inst ["):
            # inst [0 or "nvme0n1"] value 2764052
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


def tool_status():
    units = get_units()
    reachable = pmcd_reachable()
    devices = get_devices() if reachable else []
    return {
        "units": units,
        "pmcd_reachable": reachable,
        "devices": devices,
    }


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


TOOLS = [
    {
        "name": "llmsysmon_status",
        "description": "SSD telemetry stack status: pmcd/pmlogger/pmie unit states, pmcd reachability, per-device write/total await in ms",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "llmsysmon_latency",
        "description": "Live per-device SSD latency snapshot: average write await and total await in ms over a ~1.5s window",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "llmsysmon_alerts",
        "description": "Recent llmsysmon pmie alarm lines from /var/log/pcp/pmie/<host>/ssd_watch.log (empty list if none)",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "llmsysmon_install_dry_run",
        "description": "Preview the native setup: runs 'bash scripts/install.sh --json --dry-run' and returns the NDJSON event list",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
]


def handle_request(req):
    method = req.get("method")
    if method == "initialize":
        return {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "llmsysmon", "version": "1.0.0"},
        }
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        params = req.get("params", {})
        name = params.get("name")
        try:
            if name == "llmsysmon_status":
                payload = tool_status()
            elif name == "llmsysmon_latency":
                payload = tool_latency()
            elif name == "llmsysmon_alerts":
                payload = tool_alerts()
            elif name == "llmsysmon_install_dry_run":
                payload = tool_install_dry_run()
            else:
                return {
                    "content": [{"type": "text", "text": json.dumps({"error": f"Tool not found: {name}"})}],
                    "isError": True,
                }
            return {"content": [{"type": "text", "text": json.dumps(payload)}], "isError": False}
        except Exception as exc:  # pragma: no cover - defensive
            return {
                "content": [{"type": "text", "text": json.dumps({"error": str(exc)})}],
                "isError": True,
            }
    raise MethodNotFound(method)


class MethodNotFound(Exception):
    pass


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
        if result is None and "id" not in req:
            # notification, no response
            continue
        if req_id is not None:
            send({"jsonrpc": "2.0", "id": req_id, "result": result})


if __name__ == "__main__":
    main()
