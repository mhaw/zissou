#!/usr/bin/env python3
"""
Regenerate local Flask/CSRF secrets in-place.

Usage:
    python tools/rotate_local_secrets.py [--env-file .env]

The script updates (or inserts) SECRET_KEY, FLASK_SECRET_KEY, and CSRF_SECRET_KEY
with fresh 32-byte urlsafe values. A timestamped backup of the original file is created.
"""

from __future__ import annotations

import argparse
import base64
import secrets
import shutil
import sys
from pathlib import Path

ROTATED_KEYS = ("SECRET_KEY", "FLASK_SECRET_KEY", "CSRF_SECRET_KEY")


def _generate_secret() -> str:
    """Return a 32-byte urlsafe token."""
    raw = secrets.token_bytes(32)
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def rotate_env_file(env_path: Path) -> None:
    if not env_path.exists():
        raise FileNotFoundError(f"Environment file not found: {env_path}")

    backup_path = env_path.with_suffix(env_path.suffix + ".bak")
    shutil.copy2(env_path, backup_path)

    lines = env_path.read_text(encoding="utf-8").splitlines()
    present_keys = {line.split("=", 1)[0] for line in lines if "=" in line}

    replacements = {
        "SECRET_KEY": _generate_secret(),
        "FLASK_SECRET_KEY": None,  # Fill after SECRET_KEY to keep parity by default.
        "CSRF_SECRET_KEY": _generate_secret(),
    }
    # Mirror SECRET_KEY unless FLASK_SECRET_KEY already exists separately.
    if "FLASK_SECRET_KEY" not in present_keys:
        replacements["FLASK_SECRET_KEY"] = replacements["SECRET_KEY"]
    else:
        replacements["FLASK_SECRET_KEY"] = _generate_secret()

    updated_lines = []
    applied = set()
    for line in lines:
        if "=" not in line:
            updated_lines.append(line)
            continue
        key, _, _value = line.partition("=")
        key = key.strip()
        if key in ROTATED_KEYS:
            updated_lines.append(f"{key}={replacements[key]}")
            applied.add(key)
        else:
            updated_lines.append(line)

    missing_keys = [key for key in ROTATED_KEYS if key not in applied]
    if missing_keys:
        updated_lines.append("")
        for key in missing_keys:
            updated_lines.append(f"{key}={replacements[key]}")

    env_path.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")
    print(
        f"Rotated {', '.join(ROTATED_KEYS)} in {env_path} "
        f"(backup saved to {backup_path})."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rotate local Flask secrets.")
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Path to the environment file to update (default: .env)",
    )
    args = parser.parse_args(argv)

    try:
        rotate_env_file(Path(args.env_file))
    except Exception as exc:  # pragma: no cover - command-line utility
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
