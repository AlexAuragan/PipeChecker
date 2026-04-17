import argparse
import hashlib
import os
import secrets
import shutil
import subprocess
import textwrap


def _hash_key(key: str, salt: bytes) -> str:
    dk = hashlib.pbkdf2_hmac("sha256", key.encode(), salt, 100_000)
    return f"{salt.hex()}:{dk.hex()}"


def cmd_generate_key(_args):
    key = "pc_" + secrets.token_urlsafe(32)
    salt = os.urandom(16)
    hashed = _hash_key(key, salt)
    print(f"API Key  : {key}")
    print(f"Key Hash : {hashed}")
    print()
    print("Export the hash (not the key) in your environment:")
    print(f"  export PIPECHECKER_API_KEY_HASH='{hashed}'")


def cmd_setup(args):
    project_dir = os.path.abspath(args.dir or os.getcwd())
    env_path = os.path.join(project_dir, ".env")
    service_name = "pipechecker"
    service_path = f"/etc/systemd/system/{service_name}.service"
    venv_fastapi = os.path.join(project_dir, ".venv", "bin", "fastapi")

    # --- Generate API key ---
    key = "pc_" + secrets.token_urlsafe(32)
    salt = os.urandom(16)
    hashed = _hash_key(key, salt)

    # --- Write .env ---
    env_lines = {}

    # Preserve existing keys if .env already exists
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    env_lines[k.strip()] = v.strip()

    env_lines["PIPECHECKER_API_KEY_HASH"] = f"'{hashed}'"

    with open(env_path, "w") as f:
        for k, v in env_lines.items():
            f.write(f"{k}={v}\n")

    print(f"✔ API key generated")
    print(f"  Key (save this, it won't be shown again): {key}")
    print(f"  Hash written to: {env_path}")

    # --- systemd setup ---
    has_systemd = shutil.which("systemctl") is not None

    if not has_systemd:
        print("\n⚠ systemd not found — skipping service installation.")
        print(f"  To run manually: cd {project_dir} && uv run fastapi run src/api/api.py")
        return

    if os.geteuid() != 0:
        print("\n⚠ Not running as root — skipping systemd service installation.")
        print(f"  Re-run with sudo to install the service, or create {service_path} manually.")
        return

    service_content = textwrap.dedent(f"""\
        [Unit]
        Description=PipeChecker FastAPI Server
        After=network.target

        [Service]
        Type=simple
        User=root
        WorkingDirectory={project_dir}
        EnvironmentFile={env_path}
        ExecStart={venv_fastapi} run src/api/api.py --host 0.0.0.0 --port {args.port}
        Restart=on-failure
        RestartSec=5

        [Install]
        WantedBy=multi-user.target
    """)

    with open(service_path, "w") as f:
        f.write(service_content)

    print(f"\n✔ systemd service written to: {service_path}")

    try:
        subprocess.run(["systemctl", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "enable", "--now", service_name], check=True)
        print(f"✔ Service enabled and started")
        print(f"\n  Status : systemctl status {service_name}")
        print(f"  Logs   : journalctl -u {service_name} -f")
    except subprocess.CalledProcessError as e:
        print(f"✘ Failed to enable/start service: {e}")


def main():
    parser = argparse.ArgumentParser(prog="pipechecker", description="PipeChecker CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("generate-key", help="Generate a new API key and its storable hash")

    setup_parser = subparsers.add_parser("setup", help="Generate API key, write .env, and install systemd service")
    setup_parser.add_argument("--dir", default=None, help="Project directory (default: current working directory)")
    setup_parser.add_argument("--port", default=8000, type=int, help="Port to listen on (default: 8000)")

    args = parser.parse_args()

    if args.command == "generate-key":
        cmd_generate_key(args)
    elif args.command == "setup":
        cmd_setup(args)


if __name__ == "__main__":
    main()