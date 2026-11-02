"""Synthetic APT fixtures; never modify a workstation or production host."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import browser_dependencies as browser


class BrowserDependenciesTests(unittest.TestCase):
    def test_legacy_preserves_suites_components_keys_and_other_hosts(self):
        text = (
            "# http://azure.archive.ubuntu.com/ubuntu/ remains a comment\n"
            "deb [arch=amd64 signed-by=/keys/ubuntu.gpg] "
            "http://azure.archive.ubuntu.com/ubuntu/ noble-updates main universe\n"
            "deb https://packages.microsoft.com/ubuntu noble main\n"
        )
        result, count = browser.rewrite_sources(text)
        self.assertEqual(count, 1)
        self.assertEqual(
            result,
            text.replace(
                "http://azure.archive.ubuntu.com/ubuntu/ noble-updates",
                "https://archive.ubuntu.com/ubuntu/ noble-updates",
            ),
        )

    def test_deb822_preserves_all_non_uri_fields_and_continuations(self):
        text = (
            "Types: deb deb-src\nURIs: http://azure.archive.ubuntu.com/ubuntu/\n"
            " https://archive.ubuntu.com/ubuntu/\n"
            "Suites: noble noble-updates noble-security\n"
            "Components: main restricted universe multiverse\n"
            "Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n"
        )
        result, count = browser.rewrite_sources(text, True)
        self.assertEqual(count, 1)
        self.assertEqual(
            result,
            text.replace(
                "http://azure.archive.ubuntu.com/ubuntu/",
                "https://archive.ubuntu.com/ubuntu/",
            ),
        )

    def test_lookalike_host_is_unchanged(self):
        text = "deb https://azure.archive.ubuntu.com.example/ubuntu noble main\n"
        self.assertEqual(browser.rewrite_sources(text), (text, 0))

    def test_credentialed_or_unexpected_mirror_is_rejected(self):
        for uri in [
            "https://user:password@azure.archive.ubuntu.com/ubuntu",
            "https://@azure.archive.ubuntu.com/ubuntu",
            "https://:@azure.archive.ubuntu.com/ubuntu",
            "https://azure.archive.ubuntu.com:0/ubuntu",
            "https://azure.archive.ubuntu.com:444/ubuntu",
            "https://azure.archive.ubuntu.com/other",
            "https://azure.archive.ubuntu.com/ubuntu?override=1",
        ]:
            with self.subTest(uri=uri), self.assertRaises(ValueError):
                browser.rewrite_sources("deb " + uri + " noble main\n")

    def test_uri_parse_errors_do_not_expose_credentialed_nfkc_netloc(self):
        uri = "https://private-user:private-password@azure.archive.ubuntu.com\uff0fprivate-path/ubuntu/"
        for convert in (
            lambda: browser.rewrite_sources("deb " + uri + " noble main\n"),
            lambda: browser.rewrite_mirror_list(uri + " priority:1\n"),
        ):
            with self.subTest(convert=convert), self.assertRaises(ValueError) as failure:
                convert()
            self.assertIn("URI structure", str(failure.exception))
            self.assertTrue(failure.exception.__suppress_context__)
            for private in ("private-user", "private-password", "private-path"):
                self.assertNotIn(private, str(failure.exception))

    def test_invalid_source_port_value_is_redacted(self):
        with self.assertRaises(ValueError) as failure:
            browser.rewrite_sources("deb https://azure.archive.ubuntu.com:private-port/ubuntu noble main\n")
        self.assertNotIn("private-port", str(failure.exception))
        self.assertTrue(failure.exception.__suppress_context__)

    def test_refuses_local_persistent_and_unrelated_runner(self):
        for values in [
            {},
            {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "self-hosted"},
            {
                "GITHUB_ACTIONS": "true",
                "RUNNER_ENVIRONMENT": "github-hosted",
                "RUNNER_OS": "Linux",
                "GITHUB_REPOSITORY": "someone/else",
            },
        ]:
            with (
                patch.dict(os.environ, values, clear=True),
                self.assertRaises(ValueError),
            ):
                browser.require_runner()

    def test_accepts_only_the_expected_disposable_root_runner(self):
        values = {
            "GITHUB_ACTIONS": "true",
            "RUNNER_ENVIRONMENT": "github-hosted",
            "RUNNER_OS": "Linux",
            "GITHUB_REPOSITORY": "DruHustle/onesa-robot",
        }
        with (
            patch.dict(os.environ, values, clear=True),
            patch.object(browser.platform, "system", return_value="Linux"),
            patch.object(browser.os, "geteuid", return_value=0),
        ):
            browser.require_runner()

    def fixture(self, root):
        apt = root / "etc/apt"
        (apt / "sources.list.d").mkdir(parents=True)
        (apt / "apt.conf.d").mkdir()
        source = apt / "sources.list.d/ubuntu.sources"
        source.write_text(
            "URIs: http://azure.archive.ubuntu.com/ubuntu/\n"
            "Suites: noble noble-security\nSigned-By: /keys/ubuntu.gpg\n"
        )
        source.chmod(0o644)
        return apt, source

    def test_fixture_rewrite_is_idempotent_and_config_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            apt, source = self.fixture(root)
            first = browser.prepare(root)
            second = browser.prepare(root)
            self.assertEqual(first["mirror_URIs_updated"], 1)
            self.assertEqual(second["mirror_URIs_updated"], 0)
            self.assertIn("Signed-By: /keys/ubuntu.gpg", source.read_text())
            self.assertEqual(
                (apt / "apt.conf.d/99-onesa-ci-network").read_text(), browser.NETWORK
            )
            self.assertIn('APT::Update::Error-Mode "any";', browser.NETWORK)
            self.assertNotIn("AllowUnauthenticated", browser.NETWORK)
            self.assertNotIn("AllowInsecure", browser.NETWORK)

    def test_conflicting_config_preserves_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            apt, source = self.fixture(root)
            config = apt / "apt.conf.d/99-onesa-ci-network"
            config.write_text("OWNER_CONFIG\n")
            config.chmod(0o644)
            before = source.read_bytes()
            with self.assertRaises(ValueError):
                browser.prepare(root)
            self.assertEqual(source.read_bytes(), before)
            self.assertEqual(config.read_text(), "OWNER_CONFIG\n")

    def test_actual_runner_indirection_preserves_sources_and_other_mirrors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            apt, source = self.fixture(root)
            source.write_text(
                "URIs: mirror+file:/etc/apt/apt-mirrors.txt\nSuites: noble noble-security\nSigned-By: /keys/ubuntu.gpg\n"
            )
            before = source.read_text()
            mirror = apt / "apt-mirrors.txt"
            mirror.write_text(
                "# hosted Ubuntu archive failover\nhttp://azure.archive.ubuntu.com/ubuntu/\tpriority:1\nhttps://archive.ubuntu.com/ubuntu/\tpriority:2\nhttps://security.ubuntu.com/ubuntu/\tpriority:3\n"
            )
            mirror.chmod(0o644)
            result = browser.prepare(root)
            self.assertEqual(result["mirror_list_files_updated"], 1)
            self.assertEqual(result["mirror_URIs_updated"], 1)
            self.assertEqual(source.read_text(), before)
            self.assertEqual(
                mirror.read_text(),
                "# hosted Ubuntu archive failover\nhttps://archive.ubuntu.com/ubuntu/\tpriority:2\nhttps://security.ubuntu.com/ubuntu/\tpriority:3\n",
            )
            self.assertEqual(browser.prepare(root)["mirror_URIs_updated"], 0)

    def test_mirror_list_rejects_custom_entries_or_missing_canonical_archive(self):
        for text in [
            "http://azure.archive.ubuntu.com/ubuntu/ priority:1\n",
            "https://user:secret@archive.ubuntu.com/ubuntu/ priority:2\n",
            "https://unrelated.example/ubuntu/ priority:2\n",
            "https://archive.ubuntu.com/ubuntu/ shell-option\n",
        ]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                browser.rewrite_mirror_list(text)

    def test_observed_regional_mirror_preserves_existing_sources_and_priority(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            apt, source = self.fixture(root)
            source.write_text(
                "URIs: mirror+file:/etc/apt/apt-mirrors.txt\n"
                "Suites: noble noble-security\nSigned-By: /keys/ubuntu.gpg\n"
            )
            before = source.read_bytes()
            mirror = apt / "apt-mirrors.txt"
            content = (
                "http://us-east-2.ec2.archive.ubuntu.com/ubuntu/ priority:1\n"
                "https://archive.ubuntu.com/ubuntu/ priority:2\n"
                "https://security.ubuntu.com/ubuntu/ priority:3\n"
            )
            mirror.write_text(content)
            mirror.chmod(0o644)
            for _ in range(2):
                result = browser.prepare(root)
                self.assertEqual(result["mirror_URIs_updated"], 0)
                self.assertFalse(result["signing_and_suite_fields_changed"])
                self.assertEqual(source.read_bytes(), before)
                self.assertEqual(mirror.read_text(), content)
            self.assertEqual(
                (apt / "apt.conf.d/99-onesa-ci-network").read_text(), browser.NETWORK
            )

    def test_regional_failover_preserves_priority_and_only_removes_azure(self):
        regional = "http://us-east-2.ec2.archive.ubuntu.com/ubuntu/ priority:1\n"
        azure = "http://azure.archive.ubuntu.com/ubuntu/ priority:2\n"
        canonical = "https://archive.ubuntu.com/ubuntu/ priority:3\n"
        self.assertEqual(
            browser.rewrite_mirror_list(regional + azure + canonical),
            (regional + canonical, 1),
        )
        with self.assertRaisesRegex(ValueError, "signed archive transport"):
            browser.rewrite_mirror_list(regional)

    def test_regional_mirror_cannot_expand_host_or_uri_authority(self):
        host = "us-east-2.ec2.archive.ubuntu.com"
        for uri in [
            "https://" + host + ".example/ubuntu/",
            "https://subdomain." + host + "/ubuntu/",
            "https://us-east-1.ec2.archive.ubuntu.com/ubuntu/",
            "https://private-user:private-password@" + host + "/ubuntu/",
            "https://" + host + ":80/ubuntu/",
            "https://" + host + ":private-port/ubuntu/",
            "https://" + host + "/private-path/",
            "https://" + host + "/ubuntu/?private-query",
            "https://" + host + "/ubuntu/#private-fragment",
            "ftp://" + host + "/ubuntu/",
        ]:
            text = uri + " priority:1\nhttps://archive.ubuntu.com/ubuntu/ priority:2\n"
            with self.subTest(uri=uri), self.assertRaises(ValueError) as failure:
                browser.rewrite_mirror_list(text)
            for value in ("private-user", "private-password", "private-port", "private-path", "private-query", "private-fragment"):
                self.assertNotIn(value, str(failure.exception))

    def test_rejected_mirror_diagnostic_reports_only_bounded_uri_structure(self):
        private_values = ["private-user", "private-password", "private-path", "private-query", "private-fragment"]
        uri = "https://private-user:private-password@archive.ubuntu.com:443/private-path?private-query#private-fragment"
        with self.assertRaises(ValueError) as failure:
            browser.rewrite_mirror_list("# existing runner mirror\n" + uri + " priority:2\n")
        message = str(failure.exception)
        for value in private_values:
            self.assertNotIn(value, message)
        details = json.loads(message.split(": ", 1)[1])
        self.assertEqual(details, {
            "line": 2,
            "scheme": "https",
            "hostname": "archive.ubuntu.com",
            "port": 443,
            "archive_path": "redacted",
            "path_length": len("/private-path"),
            "credentials_present": True,
            "query_present": True,
            "fragment_present": True,
        })

    def test_rejected_host_and_port_remain_rejected_with_safe_diagnostics(self):
        for uri, expected_host, expected_port in [
            ("https://unlisted.example/ubuntu/", "unlisted.example", None),
            ("https://@archive.ubuntu.com/ubuntu/", "archive.ubuntu.com", None),
            ("https://:@archive.ubuntu.com/ubuntu/", "archive.ubuntu.com", None),
            ("https://archive.ubuntu.com:0/ubuntu/", "archive.ubuntu.com", 0),
            ("https://archive.ubuntu.com:443/ubuntu/", "archive.ubuntu.com", 443),
            ("https://archive.ubuntu.com:private-port/ubuntu/", "archive.ubuntu.com", "invalid"),
            ("https://private-user:private-password@archive.ubuntu.com:invalid/ubuntu/", "archive.ubuntu.com", "invalid"),
            ("https://private-user:private-password@" + "x" * 254 + "/ubuntu/", "redacted", None),
            ("https://private-user:private-password@private%20host/ubuntu/", "redacted", None),
        ]:
            with self.subTest(uri=uri), self.assertRaises(ValueError) as failure:
                browser.rewrite_mirror_list(uri + " priority:2\n")
            message = str(failure.exception)
            details = json.loads(message.split(": ", 1)[1])
            self.assertEqual(details["hostname"], expected_host)
            self.assertEqual(details["port"], expected_port)
            self.assertEqual(details["archive_path"], "/ubuntu/")
            self.assertLess(len(message), 600)
            self.assertNotIn("private-user", message)
            self.assertNotIn("private-password", message)
            self.assertNotIn("private-port", message)
            self.assertNotIn("private%20host", message)

    def test_invalid_referenced_mirror_preserves_all_existing_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            apt, source = self.fixture(root)
            source.write_text(
                source.read_text() + "URIs: mirror+file:/etc/apt/apt-mirrors.txt\n"
            )
            before = source.read_bytes()
            mirror = apt / "apt-mirrors.txt"
            mirror.write_text("https://unrelated.example/ubuntu/\n")
            mirror.chmod(0o644)
            with self.assertRaises(ValueError):
                browser.prepare(root)
            self.assertEqual(source.read_bytes(), before)
            self.assertFalse((apt / "apt.conf.d/99-onesa-ci-network").exists())

    def test_unreferenced_mirror_file_is_never_modified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            apt, _source = self.fixture(root)
            mirror = apt / "apt-mirrors.txt"
            mirror.write_text("UNRELATED OWNER CONFIGURATION\n")
            before = mirror.read_bytes()
            browser.prepare(root)
            self.assertEqual(mirror.read_bytes(), before)

    def test_referenced_mirror_alias_is_rejected_before_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            apt, source = self.fixture(root)
            source.write_text(
                "URIs: mirror+file:/etc/apt/apt-mirrors.txt\nSuites: noble\n"
            )
            (apt / "apt-mirrors.txt").symlink_to(source)
            before = source.read_bytes()
            with self.assertRaises(ValueError):
                browser.prepare(root)
            self.assertEqual(source.read_bytes(), before)
            self.assertFalse((apt / "apt.conf.d/99-onesa-ci-network").exists())

    def test_source_alias_is_rejected_before_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            apt, source = self.fixture(root)
            (apt / "sources.list.d/alias.sources").symlink_to(source)
            before = source.read_bytes()
            with self.assertRaises(ValueError):
                browser.prepare(root)
            self.assertEqual(source.read_bytes(), before)
            self.assertFalse((apt / "apt.conf.d/99-onesa-ci-network").exists())


if __name__ == "__main__":
    unittest.main()
