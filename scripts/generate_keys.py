"""Prints fresh SECRET_KEY and ENCRYPTION_KEY values for your .env.

Usage: python -m scripts.generate_keys            (print)
       python -m scripts.generate_keys --write    (append missing keys to .env)
"""

import secrets
import sys

from cryptography.fernet import Fernet

from app.core.config import BASE_DIR

if __name__ == "__main__":
    generated = {"SECRET_KEY": secrets.token_urlsafe(48), "ENCRYPTION_KEY": Fernet.generate_key().decode()}

    if "--write" not in sys.argv:
        for name, value in generated.items():
            print(f"{name}={value}")
        sys.exit(0)

    env_path = BASE_DIR / ".env"
    existing = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    present = {line.split("=", 1)[0].strip() for line in existing.splitlines() if "=" in line}
    lines = [f"{k}={v}" for k, v in generated.items() if k not in present]
    if lines:
        prefix = "" if existing.endswith("\n") or not existing else "\n"
        env_path.write_text(existing + prefix + "\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {len(lines)} new key(s) to {env_path.name}; kept {len(generated) - len(lines)} existing.")
