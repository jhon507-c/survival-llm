"""Start Survival LLM when the person signs in to Windows, so a computer that comes back on after a power cut serves
again without anyone opening it: python app/server.py --autostart on|off|status [other options].

"on" records the command in the user's Run key (HKCU, no administrator needed): the same Python, without a console
window (pythonw), this server.py, --no-browser and the other options given (--api 8770, say). It runs at sign-in, not
at power-on: for a computer nobody signs in to, Windows has to sign in by itself, and the PC has to power on by itself
after a power cut (a BIOS setting). Other systems aren't supported yet.
"""
import os
import subprocess
import sys

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
NAME = "Survival LLM"
SERVER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "server.py")
NOT_KEPT = {"--browser", "--no-browser"}  # it always starts without a window or browser


def command(args, python=None):
    """The command line that starts this server with args, without a console window."""
    python = python or sys.executable
    windowless = os.path.join(os.path.dirname(python), "pythonw.exe")
    if os.path.basename(python).lower() == "python.exe" and os.path.exists(windowless):
        python = windowless
    return subprocess.list2cmdline([python, SERVER, "--no-browser", *[a for a in args if a not in NOT_KEPT]])


def enable(args, name=NAME):
    import winreg
    line = command(args)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, line)
    return line


def disable(name=NAME):
    """True if it was set to start with Windows."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, name)
        return True
    except FileNotFoundError:
        return False


def current(name=NAME):
    """The command it starts with at sign-in, or None."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return winreg.QueryValueEx(key, name)[0]
    except FileNotFoundError:
        return None


def main(argv):
    """--autostart ACTION with the rest of argv as the options to start with. Returns the exit code."""
    at = argv.index("--autostart")
    action = argv[at + 1] if at + 1 < len(argv) else "status"
    rest = argv[1:at] + argv[at + 2:]
    if os.name != "nt":
        print("Starting with the computer is only set up on Windows so far.")
        return 1
    if action == "on":
        line = enable(rest)
        print(f"Survival LLM will start when you sign in to Windows, with: {line}\n"
              "To have it start after a power cut with nobody there, Windows has to sign in by itself, and the "
              "PC has to turn itself on when the power comes back (a BIOS setting). See FORK.md.")
    elif action == "off":
        print("Survival LLM no longer starts with Windows." if disable() else "It wasn't set to start with Windows.")
    elif action == "status":
        line = current()
        print(f"It starts when you sign in to Windows, with: {line}" if line else "It doesn't start with Windows.")
    else:
        print("Use --autostart on, off or status.")
        return 2
    return 0
