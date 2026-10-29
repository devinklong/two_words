"""Schedule the daily pipeline on this Mac with launchd so nobody has to remember to run it.

  morning : full run_daily.py once a day at 08:30 (loads yesterday, everything)
  lite    : run_daily.py --lite every 2 hours (game logs, foul scan, sleeper sync, lock inference), so the lock evidence
            is captured through game nights. Overlapping runs skip themselves (lock file in run_daily.py).

Usage (from ~/projects/two_words):
  python scripts/schedule_runs.py --print       show the two launchd files, change nothing
  python scripts/schedule_runs.py --install     write them to ~/Library/LaunchAgents and load them
  python scripts/schedule_runs.py --uninstall   unload and delete them
  python scripts/schedule_runs.py --status      show whether they are loaded

Runs only while the Mac is awake; launchd runs a missed morning run when it wakes. Logs: scripts/logs/launchd_*.log
"""
import argparse
import os
import plistlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOGS = ROOT / "scripts" / "logs"
AGENTS = Path.home() / "Library" / "LaunchAgents"
JOBS = {
    "com.two_words.morning": {"args": ["--keep-going"], "schedule": {"StartCalendarInterval": {"Hour": 8, "Minute": 30}}},
    "com.two_words.lite": {"args": ["--lite", "--keep-going"], "schedule": {"StartInterval": 7200}},
}


def plist_for(label, job):
    return {
        "Label": label,
        "ProgramArguments": [sys.executable, str(ROOT / "scripts" / "run_daily.py"), *job["args"]],
        "WorkingDirectory": str(ROOT),
        "EnvironmentVariables": {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(Path.home())},
        "StandardOutPath": str(LOGS / f"launchd_{label.split('.')[-1]}.log"),
        "StandardErrorPath": str(LOGS / f"launchd_{label.split('.')[-1]}.log"),
        **job["schedule"],
    }


def launchctl(*args):
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    for flag in ("print", "install", "uninstall", "status"):
        g.add_argument(f"--{flag}", action="store_true")
    args = ap.parse_args()

    for label, job in JOBS.items():
        path = AGENTS / f"{label}.plist"
        if args.print:
            print(f"--- {path}\n{plistlib.dumps(plist_for(label, job)).decode()}")
        elif args.install:
            LOGS.mkdir(parents=True, exist_ok=True)
            AGENTS.mkdir(parents=True, exist_ok=True)
            path.write_bytes(plistlib.dumps(plist_for(label, job)))
            launchctl("unload", str(path))
            r = launchctl("load", "-w", str(path))
            print(f"{label}: {'loaded' if r.returncode == 0 else 'FAILED ' + r.stderr.strip()}")
        elif args.uninstall:
            launchctl("unload", str(path))
            path.unlink(missing_ok=True)
            print(f"{label}: removed")
        else:
            r = launchctl("list", label)
            print(f"{label}: {'loaded' if r.returncode == 0 else 'not loaded'}")


if __name__ == "__main__":
    main()
