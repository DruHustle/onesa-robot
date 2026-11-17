const registered = "onesarobot://account/registered";

export async function registerWithOnesa(openBrowser, platform, audience, resume, origin = "https://auth.onesa.eu") {
  if (!["ios", "android"].includes(platform) || !["user", "admin"].includes(audience)) {
    throw new Error("Unsupported registration entry.");
  }
  const url = new URL("/register", origin);
  if (url.protocol !== "https:" || url.origin !== origin) throw new Error("Invalid Identity origin.");
  url.search = new URLSearchParams({ audience, product: "robot", platform }).toString();
  const result = await openBrowser(url.href, registered);
  if (result.type !== "success") return;
  if (result.url !== registered) throw new Error("Registration did not return to this app.");
  await resume();
}
