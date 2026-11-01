# Robot contract-tool minimatch adapter

This private, independently packaged adapter exposes the callable `minimatch`
function from exactly `minimatch@11.0.1`. Only `@redocly/openapi-core@1.34.20`,
whose installed URL/header matcher calls `require('minimatch')(url, pattern)`,
receives the override. No legacy matcher implementation is included, and this is
not a general compatibility layer for the entire minimatch 5 API.

The upstream fix addresses
<https://github.com/isaacs/minimatch/security/advisories/GHSA-g3hv-75wg-m249>.
Its bounded preprocessing and matcher remain unchanged. The `minimatch-fixed`
alias avoids self-resolution when the adapter is installed as `minimatch`.
The lockfile retains the official registry archive and integrity; `UPSTREAM.json`
and `UPSTREAM_LICENSE.md` retain provenance and the upstream Blue Oak license.
This package grants no new license to Onesa source.

The adapter and its archive live inside this frontend's build context. No sibling
repository or package is required. Build-time dependencies are absent from the
final frontend runtime image. Regenerate the archive from this directory with
`npm pack --ignore-scripts --pack-destination ../../vendor`, then regenerate the
frontend lockfile and verify installed consumer matching, generated contracts,
build, tests and dependency audit. Remove this adapter when the pinned consumer
supports a fixed upstream release directly.
