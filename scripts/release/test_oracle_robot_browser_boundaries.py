"""Retained frontend compatibility never substitutes for new mission authorization."""

import copy
import unittest

import oracle_robot as r
import oracle_robot_probe as probe


class BrowserAuthorizationTests(unittest.TestCase):
    def receipt(self, path):
        return {
            "authorization_checks": [
                {"engine": engine, "phase": phase, "path": path, "http_status": 401}
                for engine in ["chromium", "firefox", "webkit"]
                for phase in ["before_sign_in", "after_logout"]
            ]
        }

    def test_only_api_production_observes_retained_legacy_proxy(self):
        for environment in ["staging", "production"]:
            for component in r.COMPONENTS:
                request = {
                    "environment": environment,
                    "binding": {"component": component},
                }
                legacy = (
                    environment == "production" and r.COMPONENTS[component] == "api"
                )
                expected = "/api/v1/tasks" if legacy else "/api/v1/missions"
                probe.verify_browser_authorization(self.receipt(expected), request)
                with self.assertRaises(r.Refused):
                    probe.verify_browser_authorization(
                        self.receipt("/api/v1/missions" if legacy else "/api/v1/tasks"),
                        request,
                    )

    def test_missing_duplicated_failed_and_foreign_views_fail_closed(self):
        request = {"environment": "production", "binding": {"component": "api"}}
        receipt = self.receipt("/api/v1/tasks")
        for mutation in [
            "missing",
            "duplicate",
            "wrong_engine",
            "redirect",
            "public",
            "not_found",
        ]:
            wrong = copy.deepcopy(receipt)
            rows = wrong["authorization_checks"]
            if mutation == "missing":
                rows.pop()
            elif mutation == "duplicate":
                rows[0] = rows[1]
            elif mutation == "wrong_engine":
                rows[0]["engine"] = "foreign"
            else:
                rows[0]["http_status"] = {
                    "redirect": 302,
                    "public": 200,
                    "not_found": 404,
                }[mutation]
            with self.subTest(mutation=mutation), self.assertRaises(r.Refused):
                probe.verify_browser_authorization(wrong, request)
