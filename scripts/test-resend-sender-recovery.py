"""Regression: restored sender only after Resend accepted a test email."""
from pathlib import Path
import ast
import subprocess
prog = Path("scripts/recover-native-resend-sender.py")
wrapper = Path("scripts/repair-native-resend-403.sh")
assert prog.is_file() and wrapper.is_file()
src = prog.read_text()
ast.parse(src)
assert 'candidate = "support@mail." + domain' in src
assert 'subprocess.run(command, cwd=APP, check=False)' in src
assert src.index("result = subprocess.run(command") < src.index('funcs["write_private"](CONFIG')
assert "if result.returncode:" in src
assert 'if not (config.get("RESEND_API_KEY")' in src
assert '"DEFAULT_FROM_EMAIL": candidate' in src
assert 'shutil.copyfile(CONFIG, ENV_COPY)' in src
assert 'subprocess.run(["systemctl", "restart", "deltajanebi-web"]' in src
assert "os.chmod(backup_file, 0o600)" in src
assert subprocess.run(["bash", "-n", str(wrapper)], capture_output=True).returncode == 0
assert "scripts/update-native.sh" in wrapper.read_text()
assert "recover-native-resend-sender.py" in wrapper.read_text()
print("Legacy Resend sender recovery contract: OK")
