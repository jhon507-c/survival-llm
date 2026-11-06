"""--autostart: the command it records (pythonw, no browser, the options given) and, on Windows, setting and removing it
in the user's Run key under a test name, so the real one is left alone. No Ollama.

Usage: .venv/bin/python tests/autostart_test.py"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "app")]
import autostart  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  -> {detail}"))
    if not ok:
        FAILS.append(name)


# ---- the command ----
folder = os.path.join(tempfile.mkdtemp(), "Survival LLM", "python")
os.makedirs(folder)
for exe in ("python.exe", "pythonw.exe"):
    open(os.path.join(folder, exe), "w").close()
line = autostart.command(["--api", "8770", "--browser"], python=os.path.join(folder, "python.exe"))
check("it starts without a console window", "pythonw.exe" in line and "python.exe\"" not in line, line)
check("paths with spaces are quoted", f'"{os.path.join(folder, "pythonw.exe")}"' in line, line)
check("without a window or browser, with the options given",
      line.endswith("--no-browser --api 8770") and "--browser " not in line + " ", line)
check("this server.py", autostart.SERVER in line and autostart.SERVER.endswith("server.py"), line)
other = autostart.command([], python=os.path.join(tempfile.mkdtemp(), "python.exe"))
check("no pythonw next to it: the same Python", "python.exe" in other and "pythonw" not in other, other)

# ---- what --autostart does with the rest of the command line ----
seen = {}
autostart.enable = lambda args, name=None: seen.setdefault("on", args) or "CMD"
autostart.disable = lambda name=None: seen.setdefault("off", True)
autostart.current = lambda name=None: None
if os.name == "nt":
    code = autostart.main(["server.py", "--api", "8770", "--autostart", "on", "--api-my-files"])
    check("on: the other options are kept, --autostart and its word are not",
          code == 0 and seen.get("on") == ["--api", "8770", "--api-my-files"], (code, seen))
    check("off", autostart.main(["server.py", "--autostart", "off"]) == 0 and seen.get("off"))
    check("status, also with no word after it", autostart.main(["server.py", "--autostart"]) == 0)
    check("an unknown word", autostart.main(["server.py", "--autostart", "maybe"]) == 2)
else:
    check("other systems say it isn't set up", autostart.main(["server.py", "--autostart", "on"]) == 1)

# ---- the Run key itself, under a test name ----
if os.name == "nt":
    import importlib
    importlib.reload(autostart)
    test = "Survival LLM Test"
    try:
        wrote = autostart.enable(["--api", "8770"], name=test)
        check("on: written to the user's Run key", autostart.current(test) == wrote, autostart.current(test))
        check("off: removed", autostart.disable(test) and autostart.current(test) is None)
        check("off again: says it wasn't set", autostart.disable(test) is False)
    finally:
        autostart.disable(test)

print("all passed" if not FAILS else f"{len(FAILS)} failed")
sys.exit(1 if FAILS else 0)
