"""Pinned Vercel prebuilt operations for the same app's owner-run release bridge.

Preview and production are separate artifacts/configurations. API dependencies
are inspected as technical prerequisites, never relabelled as frontend approval.
One durable operation precedes each create; inspection never resubmits it.
"""

import json
import os
import posixpath
import re
import sqlite3
import subprocess
import sys
import tarfile
import time
import urllib.parse
import urllib.request
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parent))
import oracle_robot as r

PLAN = Path("/etc/onesa/oracle-robot-release.json")
ROOT = Path("/var/lib/onesa/release-verification/robot/frontend")
PROVIDER = Path("/etc/onesa/oracle-robot-vercel-observer.json")
CLI_AUTH = Path("/etc/onesa/oracle-vercel-cli")
APP_ROOT = "onesa-ai-robot-frontend"
API_ORIGINS = {
    "staging": "https://robot-api-staging.onesa.eu",
    "production": "https://robot-api.onesa.eu",
}

# The pinned CLI atomically updates preferences and its cache during startup.
# Keep owner custody read-only; all CLI writes belong to this container's tmpfs.
CLI_BOOTSTRAP = r"""
const fs=require('node:fs'),{spawn}=require('node:child_process');
const target='/tmp/onesa-vercel-cli';
try {
 fs.mkdirSync(target,{mode:0o700});
 for(const name of ['auth.json','config.json']) {
  const source='/private/creds/'+name,stat=fs.lstatSync(source);
  if(!stat.isFile()||stat.isSymbolicLink()||(stat.mode&0o077)||stat.size>131072)throw Error();
  JSON.parse(fs.readFileSync(source,'utf8'));
  fs.copyFileSync(source,target+'/'+name,fs.constants.COPYFILE_EXCL);
  fs.chmodSync(target+'/'+name,0o600);
 }
 const child=spawn(process.execPath,process.argv.slice(1),{
  stdio:'inherit',env:{...process.env,HOME:'/tmp',VERCEL_TELEMETRY_DISABLED:'1'}
 });
 child.once('error',()=>process.exit(71));
 child.once('exit',code=>process.exit(code??1));
} catch {
 process.stderr.write('Oracle CLI private runtime configuration unavailable; no deployment allowed.\n');
 process.exit(71);
}
"""


def provider(path, *, data=None):
    cfg = r.read(PROVIDER)
    r.require(path.startswith("/") and "://" not in path, "PROVIDER_METADATA_PATH")
    url = (
        "https://api.vercel.com"
        + path
        + ("&" if "?" in path else "?")
        + urllib.parse.urlencode({"teamId": cfg["team_id"]})
    )
    request = urllib.request.Request(
        url,
        data=None if data is None else r.canonical(data),
        headers={
            "Authorization": "Bearer " + cfg["token"],
            "Content-Type": "application/json",
            "User-Agent": "OnesaOwnerOracleRelease/1.0",
        },
    )

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_args, **_kwargs):
            return None

    with urllib.request.build_opener(NoRedirect()).open(
        request, timeout=30
    ) as response:
        raw = response.read(4 * 1024 * 1024 + 1)
        r.require(len(raw) <= 4 * 1024 * 1024, "PROVIDER_RESPONSE_BOUND")
        return json.loads(raw)


def protection(plan):
    r.require(
        plan["api_origins"] == API_ORIGINS, "CANONICAL_ROBOT_API_ORIGINS_REQUIRED"
    )
    cfg = r.read(PROVIDER)
    project = provider("/v9/projects/" + cfg["project_id"])
    fields = {
        k: project.get(k)
        for k in [
            "ssoProtection",
            "protectionBypass",
            "passwordProtection",
            "trustedIps",
        ]
    }
    import hashlib

    r.require(
        project["id"] == plan["project_id"]
        and project.get("rootDirectory") == APP_ROOT
        and project.get("framework") is None
        and project.get("nodeVersion") == "24.x"
        and project.get("serverlessFunctionRegion")
        == plan["serverless_function_region"]
        and hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()
        == plan["vercel_protection_sha256"],
        "EXISTING_PROVIDER_PROTECTION_CHANGED",
    )
    # Retrieve only the named non-secret routing variable by its recorded ID.
    # Never pull/export the project's environment or decrypt unrelated fields.
    for environment, target in [("staging", "preview"), ("production", "production")]:
        identifier = plan["vercel_api_url_variable_ids"][environment]
        r.require(
            re.fullmatch("[A-Za-z0-9_-]{8,128}", identifier),
            "NAMED_ROUTING_VARIABLE_ID",
        )
        variable = provider("/v1/projects/" + cfg["project_id"] + "/env/" + identifier)
        r.require(
            variable.get("id") == identifier
            and variable.get("key") == "ROBOT_API_URL"
            and target in variable.get("target", [])
            and variable.get("value") == plan["api_origins"][environment]
            and variable.get("gitBranch")
            in ([None, "staging"] if environment == "staging" else [None]),
            "ACTUAL_FRONTEND_API_ROUTING_CHANGED",
        )
    return project


def validate(binding):
    folder, plan = r.validate_plan(binding)
    r.require(r.base_component(binding) == "frontend", "FRONTEND_COMPONENT_SCOPE")
    if "independent_api" in plan:
        independent_api(plan, binding)
    protection(plan)
    return folder, plan


def unpack(folder, build, destination, plan):
    receipt = r.read(folder / build["receipt"])
    expected = receipt["files"]
    seen = {}
    links = {}
    total = 0
    metadata_files = {}
    entries = set()
    with tarfile.open(r.private(folder / build["archive"])) as archive:
        members = archive.getmembers()
        r.require(len(members) <= 25000, "PREBUILT_ENTRY_BOUND")
        for member in members:
            path = Path(member.name)
            r.require(member.name not in entries, "DUPLICATE_PREBUILT_PATH")
            entries.add(member.name)
            r.require(
                not path.is_absolute()
                and ".." not in path.parts
                and (member.isfile() or member.isdir() or member.issym())
                and (str(path).startswith(".vercel/") or str(path) == "vercel.json"),
                "PREBUILT_PATH_OR_TYPE",
            )
            if member.issym():
                target = posixpath.normpath(str(path.parent / member.linkname))
                r.require(
                    str(path).startswith(".vercel/output/functions/")
                    and str(path).endswith(".func")
                    and not Path(member.linkname).is_absolute()
                    and target.startswith(".vercel/output/functions/")
                    and target.endswith(".func"),
                    "PREBUILT_ALIAS_ESCAPE",
                )
                name = member.name[len(".vercel/output/") :]
                r.require(name not in links, "DUPLICATE_PREBUILT_ALIAS")
                links[name] = member.linkname
            if member.isfile():
                total += member.size
                r.require(
                    total <= 256 * 1024 * 1024 and member.size <= 32 * 1024 * 1024,
                    "PREBUILT_BYTE_BOUND",
                )
                if member.name in [".vercel/project.json", "vercel.json"]:
                    import hashlib

                    metadata_files[member.name] = hashlib.sha256(
                        archive.extractfile(member).read()
                    ).hexdigest()
                if member.name.startswith(".vercel/output/"):
                    name = member.name[len(".vercel/output/") :]
                    import hashlib

                    r.require(name not in seen, "PREBUILT_DUPLICATE_ENTRY")
                    seen[name] = hashlib.sha256(
                        archive.extractfile(member).read()
                    ).hexdigest()
        r.require(
            metadata_files == receipt["deployment_metadata_files"],
            "PREBUILT_DEPLOYMENT_METADATA_CHANGED",
        )
        r.require(
            not any(
                any(
                    entry.startswith(".vercel/output/" + link + "/")
                    for entry in entries
                )
                for link in links
            ),
            "PREBUILT_ALIAS_HAS_CHILDREN",
        )
        r.require(seen == expected, "PREBUILT_CONTENT_DIFFERS_FROM_OBSERVED_BUILD")
        r.require(
            links == receipt.get("links", {}),
            "PREBUILT_ALIASES_DIFFER_FROM_OBSERVED_BUILD",
        )
        for name, target in links.items():
            visited = {name}
            current = posixpath.normpath(
                posixpath.join(posixpath.dirname(name), target)
            )
            while current in links:
                r.require(
                    current not in visited and len(visited) < 32, "PREBUILT_ALIAS_CYCLE"
                )
                visited.add(current)
                current = posixpath.normpath(
                    posixpath.join(posixpath.dirname(current), links[current])
                )
            r.require(
                any(key.startswith(current + "/") for key in seen),
                "PREBUILT_ALIAS_TARGET_MISSING",
            )
        archive.extractall(destination, filter="data")
    project = json.loads((destination / ".vercel/project.json").read_bytes())
    cfg = r.read(PROVIDER)
    r.require(
        project["projectId"] == cfg["project_id"]
        and project["orgId"] == cfg["team_id"],
        "PREBUILT_PROJECT_SCOPE",
    )
    r.require(
        project.get("settings", {}).get("rootDirectory") == APP_ROOT,
        "PREBUILT_APPLICATION_ROOT_SCOPE",
    )
    r.require(
        json.loads((destination / "vercel.json").read_bytes()).get("regions")
        == [plan["serverless_function_region"]],
        "PRESERVE_EXISTING_FUNCTION_REGION",
    )
    # The CLI resolves the provider's monorepo root even for --prebuilt.
    # Only its empty directory is needed; candidate source is never executed.
    (destination / APP_ROOT).mkdir(mode=0o700, parents=True, exist_ok=False)


def deployments_for(operation, plan):
    response = provider(
        "/v6/deployments?"
        + urllib.parse.urlencode({"projectId": plan["project_id"], "limit": 100})
    )
    matches = [
        d
        for d in response["deployments"]
        if d.get("meta", {}).get("onesaOracleOperation") == operation
    ]
    r.require(len(matches) <= 1, "DUPLICATE_PROVIDER_OPERATIONS_INSPECT_ONLY")
    return matches


def check_deployment(identifier, binding, env, plan):
    row = provider("/v13/deployments/" + identifier)
    source = row.get("meta", {}).get("githubCommitSha") or row.get("gitSource", {}).get(
        "sha"
    )
    r.require(
        row["id"] == identifier
        and row["projectId"] == plan["project_id"]
        and source == binding["candidate_commit"]
        and row.get("target") == ("production" if env == "production" else None)
        and row.get("regions") == [plan["serverless_function_region"]],
        "ACTUAL_FRONTEND_DEPLOYMENT_BINDING",
    )
    return row


def observe_provider(binding, env, plan, operation, *, observer=None):
    # Provider custody remains in the pinned host process. The browser tool gets
    # only bounded metadata plus the existing Preview protection bypass, never
    # a provider API token or deployment credential.
    cfg = r.read(PROVIDER)
    project = protection(plan)
    recovered_frontend = None
    if observer is not None:
        r.require(
            env == "production" and binding["component"] == "api",
            "READ_ONLY_API_FRONTEND_OBSERVER_SCOPE",
        )
        recovered_frontend = observer.frontend_dependency()
    if env == "staging":
        preview = r.read(Path(plan["preview_receipt_path"]))
        r.require(
            preview["environment"] == "staging"
            and all(
                preview["binding"][k] == binding[k]
                for k in [
                    "repository",
                    "candidate_commit",
                    "source_tree",
                    "source_sha256",
                ]
            ),
            "CURRENT_SOURCE_PREVIEW_REQUIRED",
        )
        identifier = preview["deployment_id"]
        source = binding["candidate_commit"]
        origin = cfg["staging_origin"]
    else:
        identifier = (
            plan.get("current_frontend_deployment_id")
            if r.base_component(binding) == "frontend"
            else recovered_frontend["deployment_id"]
            if recovered_frontend is not None
            else cfg["production_deployment_id"]
        )
        source = (
            binding["candidate_commit"]
            if r.base_component(binding) == "frontend"
            else plan["retained_frontend"]["source"]
        )
        origin = "https://robot.onesa.eu"
        if binding["component"] == "identity-api":
            baseline = plan["identity"]["environments"][env]["baseline_frontend"]
            r.require(
                identifier == baseline["deployment_id"]
                and source == baseline["source"],
                "ACCEPTED_NATIVE_BRIDGE_MUST_SERVE_IDENTITY_API_ACCEPTANCE",
            )
    dep = provider("/v13/deployments/" + identifier)
    actual = dep.get("meta", {}).get("githubCommitSha") or dep.get("gitSource", {}).get(
        "sha"
    )
    r.require(
        dep["id"] == identifier
        and dep["projectId"] == cfg["project_id"]
        and actual == source
        and dep["readyState"] == "READY"
        and dep.get("target") == ("production" if env == "production" else None),
        "ACTUAL_PROVIDER_READBACK_MISMATCH",
    )
    alias = provider("/v4/aliases/" + origin.removeprefix("https://"))
    r.require(
        alias["deployment"]["id"] == identifier
        and alias["projectId"] == cfg["project_id"],
        "ACTUAL_ALIAS_READBACK_MISMATCH",
    )
    bypass = None
    if env == "staging":
        keys = [
            k
            for k, v in project.get("protectionBypass", {}).items()
            if v.get("scope") == "automation-bypass"
        ]
        r.require(len(keys) == 1, "EXISTING_PREVIEW_PROTECTION_CUSTODY")
        bypass = keys[0]
    return {
        "schema_version": 1,
        "binding": binding,
        "operation_id": operation,
        "environment": env,
        "observed_at": r.now().isoformat(),
        "origin": origin,
        "bypass": bypass,
        "project_id": cfg["project_id"],
        "provider": {
            "deployment_id": identifier,
            "project_id": cfg["project_id"],
            "source": source,
            "target": dep.get("target"),
        },
        "provider_token_included": False,
    }


def create(binding, env, operation, folder, plan):
    r.require(str(UUID(operation)) == operation, "CANONICAL_PROVIDER_OPERATION")
    op = folder / "operations" / operation
    op.mkdir(mode=0o700, exist_ok=False)
    r.require(
        not deployments_for(operation, plan), "EXISTING_PROVIDER_OPERATION_INSPECT_ONLY"
    )
    workspace = op / "prebuilt"
    workspace.mkdir(mode=0o700)
    unpack(folder, plan["builds"][env], workspace, plan)
    before = protection(plan)
    r.private(CLI_AUTH, directory=True)
    r.private(CLI_AUTH / "auth.json")
    r.private(CLI_AUTH / "config.json")
    r.write(
        op / "intent.json",
        {
            "binding": binding,
            "operation_id": operation,
            "environment": env,
            "artifact_sha256": plan["builds"][env]["archive_sha256"],
            "provider_create_budget": 1,
            "automatic_retry": False,
        },
    )
    guard = r.private(Path(__file__).with_name("oracle_vercel_transport_guard.cjs"))
    r.require(
        r.sha(guard) == r.trusted_toolchain(plan)["transport_guard_sha256"],
        "PINNED_DEPLOYMENT_TRANSPORT_REQUIRED",
    )
    transport = op / "transport"
    transport.mkdir(mode=0o700)
    r.write(
        transport / "scope.json",
        {
            "repository": binding["repository"],
            "component": binding["component"],
            "candidate_commit": binding["candidate_commit"],
            "environment": env,
            "operation_id": operation,
            "request_budget": 1,
            "production_region": plan["serverless_function_region"]
            if env == "production"
            else None,
        },
    )
    command = [
        "/usr/bin/docker",
        "run",
        "--rm",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,mode=700,size=256m",
        "--memory",
        "512m",
        "--cpus",
        "1",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "-v",
        str(workspace) + ":/workspace:ro",
        "-v",
        str(CLI_AUTH) + ":/private/creds:ro",
        "-v",
        str(guard) + ":/private/guard.cjs:ro",
        "-v",
        str(transport) + ":/private/transport:rw",
        "-e",
        "ONESA_ORACLE_VERIFIER_PRELOAD=1",
        "--entrypoint",
        "node",
        "-w",
        "/workspace",
        plan["cli_image"],
        "-e",
        CLI_BOOTSTRAP,
        "--",
        "--require",
        "/private/guard.cjs",
        "/opt/onesa-vercel-tool/node_modules/vercel/dist/index.js",
        "deploy",
        "--prebuilt",
        "--yes",
        "--no-wait",
        "--json",
        "--global-config",
        "/tmp/onesa-vercel-cli",
        "--project",
        plan["project_id"],
        "--env",
        "ROBOT_API_URL=" + plan["api_origins"][env],
        "--meta",
        "githubCommitRef=" + ("main" if env == "production" else "staging"),
        "--meta",
        "githubCommitSha=" + binding["candidate_commit"],
        "--meta",
        "onesaOracleOperation=" + operation,
        *(["--prod"] if env == "production" else ["--target=preview"]),
    ]
    with (op / "cli.stdout").open("xb") as out, (op / "cli.stderr").open("xb") as err:
        os.chmod(out.name, 0o600)
        os.chmod(err.name, 0o600)
        try:
            result = subprocess.run(
                command,
                stdout=out,
                stderr=err,
                timeout=600,
                check=False,
                env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
            )
            exit_code = result.returncode
        except subprocess.TimeoutExpired:
            exit_code = None
    # Never repeat create after an ambiguous response. Reconcile by operation UUID.
    deadline = time.monotonic() + 120
    matches = []
    while not matches:
        matches = deployments_for(operation, plan)
        if matches:
            break
        r.require(
            time.monotonic() < deadline,
            "PROVIDER_REQUEST_UNCERTAIN_INSPECT_WITHOUT_CREATE",
        )
        time.sleep(2)
    r.require(
        (transport / "create-request.json").is_file(),
        "SDK_TRANSPORT_NOT_OBSERVED_INSPECT_ONLY",
    )
    identifier = matches[0]["uid"]
    dep = check_deployment(identifier, binding, env, plan)
    receipt = {
        "binding": binding,
        "operation_id": operation,
        "environment": env,
        "deployment_id": identifier,
        "cli_exit": exit_code,
        "provider_state": dep["readyState"],
        "artifact_sha256": plan["builds"][env]["archive_sha256"],
        "transport_receipts": {
            name: r.sha(r.private(transport / name))
            for name in [
                "create-request.json",
                "missing-files-response.json",
                "create-continuation-request.json",
            ]
            if (transport / name).exists()
        },
        "observed_at": r.now().isoformat(),
    }
    r.write(op / "provider-receipt.json", receipt)
    protection(plan)
    r.require(before["id"] == plan["project_id"], "PROJECT_PRESERVATION")
    deadline = time.monotonic() + 600
    while dep["readyState"] not in ["READY", "ERROR", "CANCELED"]:
        r.require(time.monotonic() < deadline, "DEPLOYMENT_NOT_READY_INSPECT_ONLY")
        time.sleep(2)
        dep = check_deployment(identifier, binding, env, plan)
    r.require(dep["readyState"] == "READY", "PROVIDER_DEPLOYMENT_FAILED_RETAIN_RECEIPT")
    if env == "staging":
        alias = r.read(PROVIDER)["staging_origin"].removeprefix("https://")
        r.require(
            re.fullmatch("onesa-ai-robot-git-staging-[a-z0-9-]+.vercel.app", alias),
            "EXISTING_STAGE_ALIAS_ONLY",
        )
        r.write(
            op / "alias-intent.json",
            {
                "deployment_id": identifier,
                "alias": alias,
                "operation_id": operation,
                "request_budget": 1,
            },
        )
        provider("/v2/deployments/" + identifier + "/aliases", data={"alias": alias})
        observed = provider("/v4/aliases/" + alias)
        r.require(
            observed["deployment"]["id"] == identifier
            and observed["projectId"] == plan["project_id"],
            "STAGE_ALIAS_READBACK_FAILED",
        )
    return receipt, op / "provider-receipt.json"


def prepare_preview(api_binding, context, api_plan):
    binding = r.plan_binding(api_plan, "frontend")
    r.require(
        all(
            binding[k] == api_binding[k]
            for k in ["repository", "candidate_commit", "source_tree", "source_sha256"]
        ),
        "PREVIEW_SOURCE_DEPENDENCY",
    )
    folder, plan = validate(binding)
    _receipt, path = create(binding, "staging", context["context_id"], folder, plan)
    return path


def verify_static(binding, env, folder, plan, operation):
    """Compare real served static bytes, not only a provider success status."""
    observation = observe_provider(binding, env, plan, operation)
    receipt = r.read(folder / plan["builds"][env]["receipt"])
    hashes = receipt["served_static_files"]
    r.require(hashes and len(hashes) < 20000, "STATIC_BUILD_MANIFEST_REQUIRED")
    import hashlib

    for name, expected in hashes.items():
        r.require(
            name.startswith("/")
            and ".." not in name
            and "?" not in name
            and "\\" not in name,
            "STATIC_ASSET_PATH_SCOPE",
        )
        headers = {"User-Agent": "OnesaOwnerOracleRelease/1.0"}
        if observation["bypass"]:
            headers["x-vercel-protection-bypass"] = observation["bypass"]
        request = urllib.request.Request(observation["origin"] + name, headers=headers)
        with urllib.request.build_opener(r.NoRedirect()).open(
            request, timeout=30
        ) as response:
            raw = response.read(4 * 1024 * 1024 + 1)
        r.require(
            len(raw) <= 4 * 1024 * 1024 and hashlib.sha256(raw).hexdigest() == expected,
            "ACTUAL_SERVED_STATIC_ARTIFACT_MISMATCH",
        )
    return observation


def independent_api(plan, frontend_binding):
    """Inspect the original accepted API, never transfer its approval to a frontend.

    Staging is a separately identified dependency with exact equivalent backend
    source and fresh image checks. The frontend's own hosted qualification must
    exercise it; an old or revoked staging qualification is never used as authority.
    """
    import oracle_robot_recovery as recovery

    proof = plan["independent_api"]
    recovery_reference = (
        {"recovery_commission"} if "recovery_commission" in proof else set()
    )
    r.require(
        plan.get("release_component") == "frontend"
        and frontend_binding == plan["bindings"]["frontend"]
        and set(proof)
        == {
            "verifier_store",
            "operation_id",
            "staging_binding",
            "production_binding",
            "runtimes",
        }
        | recovery_reference
        and set(proof["runtimes"]) == {"staging", "production"}
        and re.fullmatch(
            r"/var/lib/onesa/release-verification/robot-owner-[a-f0-9]{40}",
            proof["verifier_store"],
        )
        and proof["verifier_store"] != plan["verifier_store"]
        and str(UUID(proof["operation_id"])) == proof["operation_id"]
        and proof["production_binding"] == plan["bindings"]["api"],
        "DIRECT_INDEPENDENT_API_REFERENCE_REQUIRED",
    )
    binding = proof["production_binding"]
    if recovery_reference:
        import oracle_frontend_repair_dependency as repair

        repair.verify(plan, frontend_binding)
    else:
        records = r.verify_invocation(
            binding,
            {"operation_id": proof["operation_id"], "read_only": True},
            "production",
            {"verifier_store": proof["verifier_store"]},
            dependency=True,
        )
        r.require(
            any(
                x["kind"] == "production-verification"
                and x["parent"] == proof["operation_id"]
                for x in records
            )
            and binding["component"] == "api"
            and binding["repository"] == "DruHustle/onesa-robot"
            and binding["target_environment"] == "production",
            "ORIGINAL_ACCEPTED_API_REQUIRED_NO_RETAINED_PROOF_CHAIN",
        )
    tool = r.trusted_toolchain(plan)
    source = {
        "repository": frontend_binding["repository"],
        **{
            k: frontend_binding[k]
            for k in ["candidate_commit", "source_tree", "source_sha256"]
        },
    }
    r.require(
        all(
            source[key] == tool[other]
            for key, other in [
                ("candidate_commit", "source_commit"),
                ("source_tree", "source_tree"),
                ("source_sha256", "source_sha256"),
            ]
        ),
        "FRONTEND_DIFFERS_FROM_COMMISSIONED_MAIN",
    )
    for env in ["staging", "production"]:
        selected = proof[env + "_binding"]
        r.require(
            selected["repository"] == binding["repository"]
            and selected["component"] == "api"
            and selected["target_environment"] == "production",
            "EXACT_SIMULATION_API_DEPENDENCY_REQUIRED",
        )
        runtime = proof["runtimes"][env]
        r.require(
            set(runtime)
            == {
                "container_id",
                "source",
                "image",
                "started_at",
                "restart_count",
                "environment_sha256",
            }
            and runtime["source"] == selected["candidate_commit"]
            and runtime["image"] == selected["artifact_digest"],
            "BOUND_INDEPENDENT_API_SOURCE_IMAGE_REQUIRED",
        )
        recovery.unchanged_components(selected, r.VERIFIER.parent, source)
        folder = r.component_folder(selected)
        r.require(
            r.sha(r.private(folder / "image.tar.gz")) == selected["build_sha256"],
            "INDEPENDENT_API_ARCHIVE_CHANGED",
        )
        scan_path = folder / "scan.json"
        if recovery_reference:
            scan_path = repair.security_path(plan, frontend_binding, env)
        r.image_security(r.read(scan_path), selected["artifact_digest"])
        r.verify_application_bytes(folder, selected)
        observed = r.local_identity(frontend_binding, env, plan)
        r.require(
            observed == proof["runtimes"][env], "INDEPENDENT_API_DEPENDENCY_CHANGED"
        )
    actual = proof["runtimes"]["production"]
    r.require(
        plan["retained_api"]
        == {k: actual[k] for k in ["image", "source", "container_id"]},
        "EXACT_RETAINED_PRODUCTION_API_REQUIRED",
    )
    return proof["operation_id"]


def accepted_api(plan):
    if "independent_api" in plan:
        return independent_api(plan, plan["bindings"]["frontend"])
    binding = r.plan_binding(plan, "api")
    store = r.private(Path(plan["verifier_store"]), directory=True)
    db = sqlite3.connect("file:" + str(store / "records.sqlite") + "?mode=ro", uri=True)
    try:
        row = db.execute(
            "SELECT d.id FROM deployments d JOIN records x ON x.id=d.id WHERE d.state='accepted' AND x.scope=? ORDER BY x.sequence DESC LIMIT 1",
            (binding["repository"] + ":" + binding["component"],),
        ).fetchone()
    finally:
        db.close()
    r.require(row, "NEW_INSPECTION_API_NOT_ACCEPTED")
    r.verify_invocation(
        binding,
        {"operation_id": row[0], "read_only": True},
        "production",
        plan,
        dependency=True,
    )
    observed = r.local_identity(binding, "production", plan)
    r.require(
        plan["retained_api"]
        == {
            "image": observed["image"],
            "source": observed["source"],
            "container_id": observed["container_id"],
        },
        "ACTUAL_API_DEPENDENCY_CHANGED",
    )
    return row[0]


def runner(phase, binding, context):
    folder, plan = validate(binding)
    chain = r.verify_invocation(binding, context, phase, plan)
    context_id = next(x["id"] for x in chain if x["kind"] == "context")
    accepted_api(plan)
    before = r.inventory()
    if phase == "staging":
        r.preflight(binding)
        receipt, _path = create(binding, "staging", context_id, folder, plan)
        plan["preview_receipt_path"] = str(_path)
        verify_static(binding, "staging", folder, plan, context_id)
        value = r.probe(binding, "staging", context_id, folder, plan)
        value["checks"] = {k: "PASS" for k in r.CHECKS["staging"]}
        value["deployment_id"] = receipt["deployment_id"]
    elif phase == "operations":
        value = r.operational_observations(
            binding,
            plan,
            folder,
            context_id,
            qualification=next(
                (item for item in chain if item["kind"] == "qualification"), None
            ),
        )
    elif phase == "deploy":
        receipt, _path = create(
            binding, "production", context["operation_id"], folder, plan
        )
        plan["current_frontend_deployment_id"] = receipt["deployment_id"]
        value = {**receipt, "checks": {k: "PASS" for k in r.CHECKS["deploy"]}}
    else:
        operation = context["operation_id"]
        path = folder / "operations" / operation / "provider-receipt.json"
        if path.exists():
            receipt = r.read(path)
        else:
            # GET-only reconciliation after an interrupted CLI acknowledgement.
            # Keep the existing intent and never call create again.
            intent = r.read(path.parent / "intent.json")
            r.require(
                intent["binding"] == binding
                and intent["operation_id"] == operation
                and intent["environment"] == "production",
                "EXISTING_PROVIDER_INTENT_REQUIRED",
            )
            matches = deployments_for(operation, plan)
            r.require(len(matches) == 1, "PROVIDER_REQUEST_UNCERTAIN_INSPECT_ONLY")
            dep = check_deployment(matches[0]["uid"], binding, "production", plan)
            r.require(
                dep["readyState"] == "READY", "EXISTING_PROVIDER_OPERATION_NOT_READY"
            )
            receipt = {
                "binding": binding,
                "operation_id": operation,
                "environment": "production",
                "deployment_id": dep["id"],
                "artifact_sha256": plan["builds"]["production"]["archive_sha256"],
                "observed_at": r.now().isoformat(),
                "reconciled_read_only": True,
            }
            r.write(path, receipt)
        r.require(
            receipt["binding"] == binding and receipt["operation_id"] == operation,
            "DURABLE_FRONTEND_RECEIPT_REQUIRED",
        )
        plan["current_frontend_deployment_id"] = receipt["deployment_id"]
        verify_static(binding, "production", folder, plan, operation)
        value = r.probe(
            binding,
            "production",
            operation + "-" + str(time.time_ns()),
            folder,
            plan,
            read_only=True,
        )
        value["operation_id"] = operation
        value["deployment_id"] = receipt["deployment_id"]
        value["checks"] = {k: "PASS" for k in r.CHECKS["production"]}
    r.unchanged(before, r.inventory())
    return {
        **value,
        "binding": binding,
        "environment": "staging" if phase == "staging" else "production",
        "workflow_run_id": 0,
        "observed_at": r.now().isoformat(),
    }


def main():
    r.require(
        os.geteuid() == 0 and not os.getenv("GITHUB_ACTIONS"),
        "EXTERNAL_OWNER_FRONTEND_ONLY",
    )
    r.require(len(sys.argv) == 2 and sys.argv[1] in r.CHECKS, "FRONTEND_PHASE_SCOPE")
    raw = sys.stdin.buffer.read(131073)
    r.require(len(raw) <= 131072, "FRONTEND_REQUEST_BOUND")
    request = json.loads(raw)
    r.require(set(request) == {"binding", "context"}, "FRONTEND_REQUEST_SCHEMA")
    try:
        result = runner(sys.argv[1], request["binding"], request["context"])
    except (
        r.Refused,
        KeyError,
        ValueError,
        TypeError,
        OSError,
        subprocess.SubprocessError,
    ) as error:
        code = r.record_failure(sys.argv[1], request, error)
        raise r.Refused(code) from None
    print(json.dumps(result, allow_nan=False))


if __name__ == "__main__":
    try:
        main()
    except (r.Refused, KeyError, ValueError, OSError, subprocess.SubprocessError):
        raise SystemExit(
            "Robot frontend operation refused; inspect retained private evidence without repeating provider requests."
        ) from None
