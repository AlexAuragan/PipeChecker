import argparse
import hashlib
import os
import secrets


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


def main():
    parser = argparse.ArgumentParser(prog="pipechecker", description="PipeChecker CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("generate-key", help="Generate a new API key and its storable hash")

    args = parser.parse_args()

    if args.command == "generate-key":
        cmd_generate_key(args)


if __name__ == "__main__":
    main()
