#!/usr/bin/env python3
"""Native email setup must preserve configuration and avoid exposed API keys."""
from pathlib import Path
import ast
src = Path("scripts/configure-native-email.py").read_text()
ast.parse(src)
check = Path("scripts/check-native-email.py").read_text()
ast.parse(check)
shell = Path("scripts/repair-native-email.sh").read_text()
assert "native.env" in src and "getpass.getpass" in src
assert "os.chmod(temporary, 0o640)" in src
assert "shutil.copyfile(CONFIG, ENV_COPY)" in src
assert 'subprocess.run(["systemctl", "restart", "deltajanebi-web"]' in src
# Regression for the actual failure: r+ uses a seek-requiring buffered
# stream and may raise io.UnsupportedOperation on non-seekable SSH terminals.
assert 'open("/dev/tty", "r+"' not in src
assert 'open("/dev/tty", "r", encoding="utf8"' in src
assert 'open("/dev/tty", "w", encoding="utf8"' in src
assert "native-env-exec.py" in src
assert "getpass" in src and "api_key" in src
assert "sudo cat" not in shell and "echo $SMTP_PASSWORD" not in shell
assert "scripts/update-native.sh" in shell and "configure-native-email.py" in shell
assert "fail_silently=False" in check
# Exercise the real input path on a Linux pseudo-terminal, with the script
# loaded exactly as the native installer does. This reproduces the SSH/piped
# installer failure without requiring a live Resend key or sending emails.
import os
import pty
import runpy
import select
import signal
import time

pid, master = pty.fork()
if pid == 0:
    try:
        value = runpy.run_path("scripts/configure-native-email.py")["prompt"]("Verified sender email: ")
        print("TTY_RESULT=" + value, flush=True)
        os._exit(0)
    except Exception as exc:
        print("TTY_ERROR=" + repr(exc), flush=True)
        os._exit(2)

output = b""
provided = False
deadline = time.monotonic() + 10
try:
    while time.monotonic() < deadline and b"TTY_RESULT=" not in output and b"TTY_ERROR=" not in output:
        ready, _, _ = select.select([master], [], [], 0.2)
        if ready:
            try:
                output += os.read(master, 2048)
            except OSError:
                break
        if not provided and b"Verified sender email: " in output:
            os.write(master, b"support@mail.example.com\n")
            provided = True
    if b"TTY_RESULT=support@mail.example.com" not in output:
        os.kill(pid, signal.SIGKILL)
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 0, output.decode(errors="replace")
    assert b"TTY_RESULT=support@mail.example.com" in output, output.decode(errors="replace")
finally:
    os.close(master)

# Integration regression: execute check-native-email.py directly from outside
# the project (the customer's shell is /root), without making any HTTP calls.
import subprocess
import tempfile
import sys
with tempfile.TemporaryDirectory() as outside:
    program = Path("scripts/check-native-email.py").resolve()
    result = subprocess.run(
        [sys.executable, str(program), "--check-imports"],
        cwd=outside,
        capture_output=True,
        text=True,
        timeout=20,
        env=os.environ.copy(),
    )
    assert result.returncode == 0, result.stderr
    assert "Native Django project imports: OK" in result.stdout

# The retest command may be invoked by piping a GitHub script into Bash.
# Ensure it stays valid Bash and never asks for provider secrets a second time.
command = Path("scripts/verify-native-email.sh")
assert command.is_file()
assert "scripts/update-native.sh" in command.read_text()
assert "--check-imports" in command.read_text()
assert "check-native-email.py" in command.read_text()
assert subprocess.run(["bash", "-n", str(command)], capture_output=True).returncode == 0

print("Native Resend repair and confidential credentials contract: OK")
