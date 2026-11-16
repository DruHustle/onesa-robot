"""Offline source checks for Oracle API isolation; no Docker daemon or runtime secrets."""

import json
import unittest
from pathlib import Path

from evidence import PRODUCT

ROOT = Path(__file__).resolve().parents[2]
POLICY = {
    "onesa-company-portal": ("portal", 18301, 18300, 8300),
    "onesa-ai-platform": ("ai", 18001, 18000, 8000),
    "onesa-robot": ("robot", 18432, 18431, 8431),
}


class OracleBackendConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = json.loads((ROOT / "compose.oracle-backend.yaml").read_text())
        cls.product, cls.stage_port, cls.prod_port, cls.target_port = POLICY[PRODUCT]

    def test_explicit_profiles_required_image_pins_and_source_labels(self):
        self.assertEqual(set(self.config["services"]), {"staging", "production"})
        for name in ("staging", "production"):
            service = self.config["services"][name]
            self.assertEqual(service["profiles"], [name])
            self.assertTrue(
                service["image"].startswith("${ORACLE_" + name.upper() + "_IMAGE:?")
            )
            self.assertTrue(
                service["labels"]["org.onesa.source-commit"].startswith(
                    "${ORACLE_" + name.upper() + "_COMMIT:?"
                )
            )
            self.assertEqual(service["labels"]["org.onesa.environment"], name)
            self.assertEqual(service["platform"], "linux/arm64")
            self.assertEqual(service["pull_policy"], "never")
            self.assertNotIn("build", service)

    def test_loopback_ports_separate_networks_and_private_runtime_files(self):
        for name, port in (
            ("staging", self.stage_port),
            ("production", self.prod_port),
        ):
            service = self.config["services"][name]
            self.assertEqual(service["ports"], [f"127.0.0.1:{port}:{self.target_port}"])
            self.assertEqual(service["networks"], [name])
            self.assertEqual(
                service["env_file"][0]["path"],
                f"/etc/onesa/backends/{self.product}-{name}.env",
            )
            self.assertEqual(service["env_file"][0]["format"], "raw")
            for volume in service.get("volumes", []):
                self.assertEqual(volume, f"{name}_storage:/app/storage")

    def test_resources_and_hardening_keep_staging_lower_priority(self):
        stage, prod = (
            self.config["services"][name] for name in ("staging", "production")
        )
        self.assertLess(stage["cpu_shares"], prod["cpu_shares"])
        self.assertLess(float(stage["cpus"]), float(prod["cpus"]))
        for service in (stage, prod):
            self.assertTrue(service["read_only"])
            self.assertEqual(service["cap_drop"], ["ALL"])
            self.assertIn("no-new-privileges:true", service["security_opt"])
            self.assertIn(service["mem_limit"], {"192m", "256m", "384m", "512m"})
            self.assertEqual(service["logging"]["options"]["max-file"], "3")
            self.assertNotIn("privileged", service)
            self.assertNotIn("network_mode", service)


if __name__ == "__main__":
    unittest.main()
