"""Offline publication gate. Print paths/rules only, never secret values."""

import argparse
import hashlib
import io
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = {
    "private-key": rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----",
    "aws-access-key": rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b",
    "github-token": rb"\bgh[pousr]_[A-Za-z0-9]{36,255}\b|\bgithub_pat_[A-Za-z0-9_]{40,}\b",
    "provider-token": rb"\bsk-(?:proj-)?[A-Za-z0-9_-]{40,}\b",
    "resend-token": rb"\bre_[A-Za-z0-9_-]{30,}\b",
    "neon-storage-key": rb"\bn(?:ak|sk)_live_[A-Za-z0-9_-]{24,}\b",
    "neon-database-password": rb"\bnpg_[A-Za-z0-9_-]{12,}\b",
    "slack-token": rb"\bxox[baprs]-[A-Za-z0-9-]{20,}\b",
    "google-api-key": rb"\bAIza[A-Za-z0-9_-]{35}\b",
    "credential-assignment": rb"(?im)^\s*(?:export\s+)?(?:[A-Z0-9_]*(?:PASSWORD|SECRET|TOKEN|API_KEY|SESSION_KEY|PRIVATE_KEY))\s*=\s*[\"']?([A-Za-z0-9_+/=-]{20,})",
}
RULES = {key: re.compile(value) for key, value in PATTERNS.items()}
DENIED_PARTS = {
    "data",
    "storage",
    "uploads",
    "backups",
    "private",
    "confidential",
    "corporate-records",
    "secrets",
    "node_modules",
    ".next",
    ".venv",
    "__pycache__",
    "test-results",
    "playwright-report",
    "checkpoints",
    "model-weights",
}
DENIED_SUFFIXES = {
    ".dump",
    ".sqlite",
    ".sqlite3",
    ".db",
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".gguf",
    ".safetensors",
    ".ckpt",
    ".pt",
    ".pth",
    ".onnx",
    ".h5",
    ".hdf5",
    ".parquet",
    ".pdf",
    ".docx",
    ".xlsx",
    ".tsbuildinfo",
    ".pyc",
}


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args])


def denied(name):
    path = Path(name)
    if path.name in {".envrc", ".netrc", ".npmrc", ".pypirc"}:
        return True
    if set(path.parts) & {".aws", ".ssh", ".kube", ".terraform"}:
        return True
    if (
        ".tfstate" in path.name
        or path.name.startswith("credentials")
        and path.suffix == ".json"
    ):
        return True
    if "service-account" in path.name and path.suffix == ".json":
        return True
    example = path.name.endswith(".env.example") or path.name == ".env.example"
    env = (path.name.startswith(".env") or ".env" in path.name) and not example
    return (
        env
        or bool(set(path.parts) & DENIED_PARTS)
        or path.suffix.lower() in DENIED_SUFFIXES
        or name.startswith(
            ("docs/generated/", "docs/development/evidence/", "assets/documents/")
        )
    )


def reviewed_telemetry_fixture(name, data, value):
    # This exact historical unit-test snapshot contained a deliberately fake marker.
    # It performed only local SHA256/boolean assertions, never authentication.
    # Bind all three fields: no runtime source or changed fixture is exempted.
    return (
        name == "scripts/release/test_staging_configuration.py"
        and value == b"synthetic-private-edge-marker"
        and hashlib.sha256(data).hexdigest()
        == "0784c4944a08aeca974d35f2e663f9f189deb42307a66e67b4d32d2b51f75244"
    )


def secrets(data, name):
    # Decode PDF text when pypdf is available; otherwise raw-byte scanning only.
    if name.lower().endswith(".pdf"):
        try:
            from pypdf import PdfReader

            data += "\n".join(
                page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages
            ).encode()
        except ImportError:
            pass
        except Exception:  # noqa: BLE001 - malformed/untrusted PDFs require manual review.
            return ["unreadable-pdf-review-required"]
    results = []
    for rule, pattern in RULES.items():
        matches = list(pattern.finditer(data))
        if rule == "credential-assignment":
            # Documented non-credential examples/test fixtures still need review.
            matches = [
                m
                for m in matches
                # Reviewed diagnostic redaction sentinel is not an authenticating key.
                # Keep this exception bound to its exact value and test source path.
                if not (
                    (
                        name == "scripts/test_inspect_hosting.py"
                        and m.group(1)
                        == b"synthetic-sensitive-fixture-must-not-be-emitted"
                    )
                    or reviewed_telemetry_fixture(name, data, m.group(1))
                )
                and not any(
                    marker in m.group(1).lower()
                    for marker in (
                        b"placeholder",
                        b"replace",
                        b"example",
                        b"test",
                        b"change",
                        b"your_",
                    )
                )
            ]
        if matches:
            results.append(rule)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--monorepo-root", type=Path)
    args = parser.parse_args()
    repository_root = Path(git("rev-parse", "--show-toplevel").decode().strip()).resolve()
    if repository_root != ROOT:
        gate = repository_root / "scripts/repository_hygiene.py"
        if (
            args.monorepo_root is None
            or args.monorepo_root.resolve() != repository_root
            or ROOT.parent != repository_root
            or ROOT.name not in {"onesa-company-portal", "onesa-ai-platform", "onesa-robot", "onesa-drone"}
            or gate.is_symlink()
            or not gate.is_file()
        ):
            raise SystemExit("Refusing to scan an inherited ancestor repository")
        command = [sys.executable, str(gate), "--product", ROOT.name]
        command += ["--history"] if args.history else []
        command += ["--json"] if args.json else []
        raise SystemExit(subprocess.run(command, cwd=repository_root, check=False).returncode)
    if args.monorepo_root is not None:
        raise SystemExit("Monorepo mode requires the explicit consolidated checkout")
    findings = []

    def inspect(scope, name, data):
        if denied(name):
            findings.append(
                {"scope": scope, "path": name, "rule": "restricted-or-generated-path"}
            )
        for rule in secrets(data, name):
            findings.append({"scope": scope, "path": name, "rule": rule})

    entries = git("ls-files", "--stage", "-z").split(b"\0")
    for entry in entries:
        if not entry:
            continue
        metadata, raw_name = entry.split(b"\t", 1)
        _, oid, _ = metadata.split()
        name = raw_name.decode(errors="replace")
        inspect("index", name, git("cat-file", "blob", oid.decode()))
    names = (
        git("ls-files", "--cached", "--others", "--exclude-standard", "-z")
        .decode()
        .split("\0")
    )
    for name in set(names) - {""}:
        path = ROOT / name
        if path.is_file() and not path.is_symlink():
            inspect("worktree", name, path.read_bytes())
    if args.history:
        commits = git("rev-list", "--all").decode().splitlines()
        seen = set()
        for commit in commits:
            for entry in git("ls-tree", "-r", "-z", commit).split(b"\0"):
                if not entry:
                    continue
                metadata, raw_name = entry.split(b"\t", 1)
                _, kind, oid = metadata.split()
                name = raw_name.decode(errors="replace")
                if kind != b"blob" or (oid, name) in seen:
                    continue
                seen.add((oid, name))
                inspect(
                    "history:" + commit[:12],
                    name,
                    git("cat-file", "blob", oid.decode()),
                )
    findings.sort(key=lambda item: (item["scope"], item["path"], item["rule"]))
    if args.json:
        print(
            json.dumps(
                {"history_scanned": args.history, "findings": findings}, indent=2
            )
        )
    else:
        for finding in findings:
            print("{scope}: {path} [{rule}]".format(**finding))
        print(
            f"Publication hygiene: {len(findings)} finding(s); manual review still required."
        )
    raise SystemExit(bool(findings))


if __name__ == "__main__":
    main()
