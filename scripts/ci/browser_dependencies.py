"""Prepare signed Ubuntu browser dependencies on disposable GitHub runners only."""

import json
import os
import platform
import re
import stat
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

NETWORK = (
    'Acquire::http::Timeout "20";\n'
    'Acquire::https::Timeout "20";\n'
    'Acquire::Retries "1";\n'
    'APT::Update::Error-Mode "any";\n'
)


def rewrite_sources(text, deb822=False, mirror_files=None):
    """Change only the observed Azure mirror URI; retain trust and suite fields."""
    changes = 0

    def replace(match):
        nonlocal changes
        value = match.group(0)
        if value.startswith("mirror+file:"):
            if (
                value == "mirror+file:/etc/apt/apt-mirrors.txt"
                and mirror_files is not None
            ):
                mirror_files.add("apt-mirrors.txt")
            return value
        try:
            uri = urlsplit(value)
        except ValueError:
            raise ValueError("Invalid Ubuntu mirror URI structure; no rewrite performed") from None
        if uri.hostname != "azure.archive.ubuntu.com":
            return value
        try:
            port = uri.port
        except ValueError:
            raise ValueError("Invalid Ubuntu mirror port; no rewrite performed") from None
        if (
            uri.username is not None
            or uri.password is not None
            or port is not None
            or uri.query
            or uri.fragment
            or uri.path not in {"/ubuntu", "/ubuntu/"}
        ):
            raise ValueError("Unexpected Ubuntu mirror URI; no rewrite performed")
        changes += 1
        return "https://archive.ubuntu.com" + uri.path

    result = []
    field = ""
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith("#"):
            result.append(line)
            continue
        if deb822:
            header = re.match(r"^([A-Za-z-]+):", line)
            if header:
                field = header.group(1).lower()
            elif not line.strip():
                field = ""
            active = field == "uris"
        else:
            active = bool(re.match(r"^\s*deb(?:-src)?\s", line))
        if active:
            content, marker, comment = line.partition("#")
            line = (
                re.sub(r"(?:https?://|mirror\+file:)[^\s]+", replace, content)
                + marker
                + comment
            )
        result.append(line)
    return "".join(result), changes


def mirror_uri_diagnostic(uri, line_number):
    """Report bounded public URI structure without credentials or arbitrary paths."""
    hostname = uri.hostname or ""
    try:
        port = uri.port
    except ValueError:
        port = "invalid"
    return {
        "line": line_number,
        "scheme": uri.scheme if uri.scheme in {"http", "https"} else "redacted",
        "hostname": hostname
        if re.fullmatch(r"[a-zA-Z0-9.-]{1,253}", hostname)
        else "redacted",
        "port": port,
        "archive_path": uri.path
        if uri.path in {"/ubuntu", "/ubuntu/"}
        else "redacted",
        "path_length": len(uri.path),
        "credentials_present": uri.username is not None or uri.password is not None,
        "query_present": bool(uri.query),
        "fragment_present": bool(uri.fragment),
    }


def rewrite_mirror_list(text):
    """Remove only Azure from the runner's referenced archive failover list."""
    if len(text.splitlines()) > 32:
        raise ValueError("Unexpected mirror-list bounds")
    output = []
    removed = 0
    canonical = False
    for line_number, line in enumerate(text.splitlines(keepends=True), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            output.append(line)
            continue
        match = re.fullmatch(
            r"(https?://[^\s]+)(?:[ \t]+priority:[1-9][0-9]*)?[ \t]*(?:\r?\n)?", line
        )
        if not match:
            raise ValueError("Unexpected runner mirror-list entry")
        try:
            uri = urlsplit(match.group(1))
        except ValueError:
            raise ValueError("Invalid runner mirror-list URI structure") from None
        try:
            port = uri.port
        except ValueError:
            port = "invalid"
        if (
            uri.scheme not in {"http", "https"}
            or uri.username is not None
            or uri.password is not None
            or port is not None
            or uri.query
            or uri.fragment
            or uri.path not in {"/ubuntu", "/ubuntu/"}
            or uri.hostname
            not in {
                "azure.archive.ubuntu.com",
                "archive.ubuntu.com",
                "security.ubuntu.com",
            }
        ):
            raise ValueError(
                "Unexpected runner mirror-list URI: "
                + json.dumps(mirror_uri_diagnostic(uri, line_number), sort_keys=True)
            )
        if uri.hostname == "azure.archive.ubuntu.com":
            removed += 1
            continue
        if uri.hostname == "archive.ubuntu.com" and uri.scheme == "https":
            canonical = True
        output.append(line)
    if not canonical:
        raise ValueError("Existing signed archive transport must remain available")
    return "".join(output), removed


def regular(path):
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):
        raise ValueError("APT source aliases are not accepted")
    value = path.stat()
    if (
        not stat.S_ISREG(value.st_mode)
        or value.st_uid != os.geteuid()
        or value.st_mode & 0o022
        or value.st_size > 256 * 1024
    ):
        raise ValueError("Unexpected APT source custody")
    return value


def atomic(path, text, mode):
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as file:
        pending = Path(file.name)
        try:
            os.fchmod(file.fileno(), mode)
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
        except BaseException:
            pending.unlink(missing_ok=True)
            raise
    try:
        os.replace(pending, path)
    finally:
        pending.unlink(missing_ok=True)


def prepare(root):
    apt = root / "etc/apt"
    parts = apt / "sources.list.d"
    files = ([apt / "sources.list"] if (apt / "sources.list").exists() else []) + [
        path for path in sorted(parts.iterdir()) if path.suffix in {".list", ".sources"}
    ]
    if not 1 <= len(files) <= 32:
        raise ValueError("Bounded existing APT source inventory required")
    replacements = []
    mirror_files = set()
    mirror_updates = 0
    for path in files:
        before = regular(path)
        text = path.read_text()
        updated, count = rewrite_sources(text, path.suffix == ".sources", mirror_files)
        if count:
            replacements.append((path, updated, stat.S_IMODE(before.st_mode), count))
    for name in sorted(mirror_files):
        path = apt / name
        before = regular(path)
        updated, count = rewrite_mirror_list(path.read_text())
        if count:
            replacements.append((path, updated, stat.S_IMODE(before.st_mode), count))
            mirror_updates += 1
    config = apt / "apt.conf.d/99-onesa-ci-network"
    if config.exists():
        regular(config)
        if config.read_text() != NETWORK:
            raise ValueError("Preserve conflicting existing APT configuration")
    elif config.is_symlink() or any(p.is_symlink() for p in config.parents):
        raise ValueError("APT configuration aliases are not accepted")
    for path, updated, mode, _ in replacements:
        atomic(path, updated, mode)
    atomic(config, NETWORK, 0o644)
    return {
        "source_files_updated": len(replacements) - mirror_updates,
        "mirror_list_files_updated": mirror_updates,
        "mirror_URIs_updated": sum(row[3] for row in replacements),
        "request_timeout_seconds": 20,
        "APT_file_retries": 1,
        "repository_update_errors": "fail",
        "signing_and_suite_fields_changed": False,
    }


def require_runner():
    if not (
        platform.system() == "Linux"
        and os.geteuid() == 0
        and os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
        and os.environ.get("RUNNER_OS") == "Linux"
        and os.environ.get("GITHUB_REPOSITORY") == "DruHustle/onesa-robot"
    ):
        raise ValueError("Only the disposable GitHub-hosted Linux CI runner is allowed")


if __name__ == "__main__":
    require_runner()
    print(json.dumps(prepare(Path("/"))))
