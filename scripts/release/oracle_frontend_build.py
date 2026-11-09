"""Credential-free owner build of exact-source Vercel output, not authorization.

Run in a private disposable directory. Only metadata-only project settings may enter the build.
The Robot build has no public or server environment values. No environment pull, provider
token, SSH key, application database or signing store is mounted in the builder.
The resulting receipt is artifact provenance; it cannot authorize deployment.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent))
import oracle_robot as r

CLI = "65.2.0"


def build(
    folder, binding, metadata, tool_image, *, environment="production", docker="docker"
):
    r.require(environment in ["staging", "production"], "BUILD_ENVIRONMENT")
    folder = Path(folder).resolve()
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    r.require(
        folder.stat().st_uid == os.getuid()
        and folder.stat().st_mode & 0o077 == 0
        and not folder.is_symlink(),
        "PRIVATE_BUILD_DIRECTORY",
    )
    archive = folder / "source.tar"
    r.require(
        archive.is_file()
        and not archive.is_symlink()
        and r.sha(archive) == binding["source_sha256"],
        "BUILD_SOURCE_ARCHIVE_IDENTITY",
    )
    # No production credential is used here. The source tree is reconstructed
    # independently from tar members before any source package script executes.
    r.source_inventory(archive, binding)
    r.require(
        set(metadata)
        == {"projectId", "orgId", "projectName", "settings", "public_environment"},
        "METADATA_ONLY_BUILD_PROFILE",
    )
    r.require(
        re.fullmatch("prj_[A-Za-z0-9]+", metadata["projectId"])
        and re.fullmatch("team_[A-Za-z0-9]+", metadata["orgId"]),
        "BUILD_PROJECT_IDENTITY",
    )
    r.require(
        set(metadata["settings"])
        == {
            "framework",
            "rootDirectory",
            "buildCommand",
            "installCommand",
            "outputDirectory",
            "nodeVersion",
        }
        and metadata["settings"]["framework"] is None
        and metadata["settings"]["rootDirectory"] == r.frontend_root(binding)
        and metadata["settings"]["nodeVersion"] == "24.x",
        "BUILD_PROJECT_SETTINGS",
    )
    env = metadata["public_environment"]
    r.require(env == {}, "ROBOT_BUILD_HAS_NO_PUBLIC_OR_SERVER_CREDENTIALS")
    r.require(
        re.fullmatch("sha256:[a-f0-9]{64}", tool_image), "IMMUTABLE_BUILD_TOOL_IMAGE"
    )

    def run(args, timeout=60):
        result = subprocess.run(
            [docker, *args], capture_output=True, timeout=timeout, check=False
        )
        r.require(
            result.returncode == 0, "CREDENTIAL_FREE_BUILD_FAILED_INSPECT_PRIVATE_LOG"
        )
        return result.stdout

    image = json.loads(run(["image", "inspect", tool_image]))[0]
    r.require(
        image["Id"] == tool_image
        and image["Architecture"] == "amd64"
        and image["Config"]["Labels"].get("org.onesa.vercel-cli") == CLI,
        "LINUX_X64_PINNED_BUILD_TOOL_REQUIRED",
    )
    operation = str(uuid4())
    work = folder / ("build-" + operation)
    work.mkdir(mode=0o700)
    receipt_path = folder / ("build-" + operation + ".json")
    r.write(
        receipt_path,
        {
            "state": "BUILD_INTENT_NOT_AUTHORIZATION",
            "operation_id": operation,
            "source": binding,
            "tool_image": tool_image,
            "provider_credentials_mounted": False,
        },
    )
    try:
        with tarfile.open(archive) as source:
            source.extractall(work, filter="data")
        chat = work / "onesa-ai-robot-frontend"
        config = work / ".vercel"
        config.mkdir(mode=0o700)
        project = {
            k: metadata[k] for k in ["projectId", "orgId", "projectName", "settings"]
        }
        # The archive is product-relative; the sealed deployment metadata retains
        # the independently validated canonical provider root.
        local_project = {
            **project,
            "settings": {
                **project["settings"],
                "rootDirectory": "onesa-ai-robot-frontend",
            },
        }
        r.write(config / "project.json", local_project)
        deployment_project = config / "project.json"
        if r.is_canonical(binding):
            deployment_project = config / "deployment-project.json"
            r.write(deployment_project, project)
        shutil.copyfile(chat / "vercel.json", work / "vercel.json")
        with os.fdopen(
            os.open(
                config
                / (
                    ".env.production.local"
                    if environment == "production"
                    else ".env.preview.local"
                ),
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
            ),
            "w",
        ) as f:
            f.write("".join(k + "=" + json.dumps(v) + "\n" for k, v in env.items()))
        args = [
            docker,
            "run",
            "--rm",
            "--platform",
            "linux/amd64",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,size=1g",
            "--memory",
            "4g",
            "--cpus",
            "2",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "-e",
            "HOME=/tmp",
            "-e",
            "VERCEL_TELEMETRY_DISABLED=1",
            "-e",
            "VERCEL_GIT_COMMIT_SHA=" + binding["candidate_commit"],
            "-v",
            str(work) + ":/workspace:rw",
            "-w",
            "/workspace",
            tool_image,
            "build",
            "--standalone",
            *(["--prod"] if environment == "production" else []),
            "--yes",
            "--global-config",
            "/tmp/empty",
        ]
        log = folder / ("build-" + operation + ".log")
        with log.open("xb") as output:
            os.chmod(log, 0o600)
            result = subprocess.run(
                args, stdout=output, stderr=subprocess.STDOUT, timeout=1800, check=False
            )
        r.require(
            result.returncode == 0, "CREDENTIAL_FREE_BUILD_FAILED_INSPECT_PRIVATE_LOG"
        )
        candidates = [
            p
            for p in [config / "output", chat / ".vercel/output"]
            if (p / "config.json").is_file()
        ]
        r.require(len(candidates) == 1, "SINGLE_BUILD_OUTPUT_REQUIRED")
        output = candidates[0]
        r.require(
            json.loads((output / "config.json").read_bytes())["version"] == 3,
            "BUILD_OUTPUT_VERSION",
        )
        files = {}
        links = {}
        static = {}
        for p in output.rglob("*"):
            if p.is_symlink():
                target = p.resolve()
                relative = str(p.relative_to(output))
                r.require(
                    relative.startswith("functions/")
                    and p.name.endswith(".func")
                    and p.is_dir()
                    and target.is_relative_to(output / "functions")
                    and target.name.endswith(".func")
                    and not Path(os.readlink(p)).is_absolute(),
                    "UNSUPPORTED_PREBUILT_ALIAS",
                )
                links[relative] = os.readlink(p)
                r.require(len(links) <= 2000, "PREBUILT_ALIAS_BOUND")
                continue
            if p.is_file():
                resolved = p.resolve()
                r.require(
                    resolved.is_relative_to(work) and not p.is_symlink(),
                    "BUILD_OUTPUT_ALIAS_OUTSIDE_SOURCE",
                )
                relative = str(p.relative_to(output))
                files[relative] = r.sha(p)
                if relative.startswith("static/") and relative.endswith(
                    (".js", ".css")
                ):
                    static["/" + relative[len("static/") :]] = files[relative]
        r.require(
            files and static and len(files) < 20000, "COMPLETE_PREBUILT_OUTPUT_REQUIRED"
        )
        artifact = folder / ("frontend-" + environment + "-" + operation + ".tar.gz")
        with tarfile.open(artifact, "w:gz", dereference=False) as target:
            target.add(output, arcname=".vercel/output")
            target.add(deployment_project, arcname=".vercel/project.json")
            target.add(chat / "vercel.json", arcname="vercel.json")
        artifact.chmod(0o600)
        receipt = {
            "state": "BUILT_NOT_QUALIFIED_OR_AUTHORIZED",
            "environment": environment,
            "operation_id": operation,
            "binding": binding,
            "builder_sha256": r.sha(Path(__file__)),
            "tool_image": tool_image,
            "cli_version": CLI,
            "architecture": "linux/amd64",
            "source_tree_verified": True,
            "provider_credentials_mounted": False,
            "metadata_sha256": hashlib.sha256(r.canonical(metadata)).hexdigest(),
            "artifact_path": str(artifact),
            "artifact_sha256": r.sha(artifact),
            "files": files,
            "deployment_metadata_files": {
                ".vercel/project.json": r.sha(deployment_project),
                "vercel.json": r.sha(chat / "vercel.json"),
            },
            "links": links,
            "served_static_files": static,
            "log_sha256": r.sha(log),
        }
        r.write(folder / ("build-" + operation + "-complete.json"), receipt)
        return receipt
    finally:
        # This is only this command's unique credential-free extraction/build dir.
        shutil.rmtree(work)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--folder", type=Path, required=True)
    p.add_argument("--binding", type=Path, required=True)
    p.add_argument("--metadata", type=Path, required=True)
    p.add_argument("--tool-image", required=True)
    p.add_argument(
        "--environment", choices=["staging", "production"], default="production"
    )
    args = p.parse_args()
    try:
        print(
            json.dumps(
                build(
                    args.folder,
                    json.loads(args.binding.read_bytes()),
                    json.loads(args.metadata.read_bytes()),
                    args.tool_image,
                    environment=args.environment,
                )
            )
        )
    except Exception:  # noqa: BLE001 -- Never expose private build input or logs on the CLI.
        raise SystemExit(
            "Credential-free frontend build refused; inspect private build receipt/log; no release authority issued"
        ) from None
