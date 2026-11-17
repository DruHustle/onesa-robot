import test from "node:test";
import assert from "node:assert/strict";
import { registerWithOnesa } from "./registration.mjs";

test("user and administrator registration resume the originating native product", async () => {
  for (const platform of ["ios", "android"]) {
    for (const audience of ["user", "admin"]) {
      let resumed = false;
      await registerWithOnesa(async (address, callback) => {
        const url = new URL(address);
        assert.equal(url.origin + url.pathname, "https://auth.onesa.eu/register");
        assert.deepEqual(Object.fromEntries(url.searchParams), { audience, product: "robot", platform });
        assert.equal(callback, "onesarobot://account/registered");
        return { type: "success", url: callback };
      }, platform, audience, () => { resumed = true; });
      assert.equal(resumed, true);
    }
  }
});

test("dismissal and unrelated callbacks cannot start a product session", async () => {
  const resume = () => assert.fail("unexpected resume");
  await registerWithOnesa(async () => ({ type: "cancel" }), "ios", "user", resume);
  await assert.rejects(registerWithOnesa(async () => ({ type: "success", url: "other://account/registered" }),
    "android", "admin", resume), /did not return/);
});
