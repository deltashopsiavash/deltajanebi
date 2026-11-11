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
print("Native Resend repair and confidential credentials contract: OK")
