# Pinned OCI downloads (#1994)

The full gate has no writable repository cache (#1867). Its Bazel role creates a
small symlink index of `/var/cache/bazel/repository-cache/content_addressable/sha256`
in `~/zuul-shared-distdir`. It indexes all shared SHA-256 downloads, including
non-OCI archives; the count alone does not prove coverage of the image pins.
Each shared `file` is exposed as `<hex>` and `sha256:<hex>`.
`--distdir` lets Bazel's downloader find pinned manifests, configs and layers by
basename and verify their SHA-256 before using them. There is no copied blob archive,
expanded-contents cache or shared action cache in this path. Missing seeds fall back
to the public registry. rules_oci requests an uncacheable token before reading
pinned content. The full job sets `--repo_env=OCI_GET_TOKEN_ALLOW_FAIL=1`, allowing
a failed exchange (including 429) to fall back to digest-verified distdir reads.
Our rules_oci patch uses curl for these optional exchanges, with no HTTP retries,
a 5-second connection timeout and a 10-second total timeout. Failed exchanges are
remembered for that registry/repository during the fetch, so each blob does not
repeat them. Without the flag, token downloads still use Bazel's downloader.
The flag requires curl on the fetching client (already installed on gate nodes).
Unseeded/private blobs still need successful registry access and authentication.

The mounted cache is read-only in check, gate and post pods. PRs never promote their
downloads. A corrupt seed cannot satisfy a digest: Bazel rejects it and fetches upstream.
The index command tolerates an absent/empty share, so a cold install still works online.

## Populate or refresh the approved seed

This is an **orchestrator operation after merge**, from a clean approved-main
checkout, following the host's mount/space checks in `deploy/thunderbox/zuul.md`.
Do not run it from a PR checkout or promote a PR job's private cache. No live
provisioning or cache write is part of this change's pre-merge verification.

1. On a trusted farm-connected client, fetch just the pinned image repositories into
   a new private staging download cache. Derive the repository list from
   `build/images/images.MODULE.bazel` (the `use_repo(oci, ...)` line's
   `image_*_linux_amd64` repositories); never maintain a second digest list.
   Run `mise exec -- bazelisk fetch --repository_cache=<staging>/repository-cache
   --repo_contents_cache= --repo=@<repository>` for each. Repository fetching is
   client work; compilation and testing remain on the farm. A failed fetch is not a
   complete seed: resolve the upstream outage before promoting it.
2. Copy only `repository-cache/content_addressable/sha256/<hex>/file` and its
   canonical-ID marker files from that staging cache into the host's download-only
   cache (`/mnt/ci/zuul-downloads/repository-cache` on the host, mounted under
   `/var/lib/zuul-downloads` in k3s). Stage each new hash directory on the same
   filesystem and rename it into place after verifying `sha256sum file` equals
   `<hex>`. Preserve existing immutable entries; do not copy `contents`, output
   bases, action caches, symlinks or credentials. Check available space in the
   bounded downloads dataset first. Files must be readable by the non-root node.
3. Run acceptance in `zuul-gate-compute`, the full job's pool. It shares the
   gate's single slot: reserve a quiet slot with the orchestrator. The
   `build-farm-mirror-images` policy refuses temporary namespaces. On a fresh
   gate pod confirm `Shared download distdir: N shared SHA-256 downloads indexed`
   with N nonzero, then fetch the same pinned repositories with
   `--repository_cache= --repo_contents_cache= --distdir=$HOME/zuul-shared-distdir`
   `--repo_env=OCI_GET_TOKEN_ALLOW_FAIL=1` and a fresh output base. Deny
   blob/manifest HTTP to both ghcr.io and docker.io and make their token endpoints
   return 429. For example, add Pod `hostAliases` mapping `ghcr.io`,
   `auth.docker.io`, `registry-1.docker.io` and `index.docker.io` to an in-pod TLS
   stub (loopback), with a temporary certificate trusted by both the Bazel JVM
   and curl (`CURL_CA_BUNDLE`). Bypass proxies for those hosts in `NO_PROXY` and
   `no_proxy`. Have the stub count requests, return 429 on token paths and reject
   all blob/manifest paths. Include docker.io: two pinned images use it.
   It must finish in seconds despite failed token requests, with no blob or
   manifest requests to either registry. Retain elapsed time, stub counts, command,
   digest list and output on the issue. Repeat from another fresh output base to
   prove pod-independent reuse. Unpatched rules_oci took about 17 minutes under
   this 429 stub: each of roughly 600 blob lookups retried the token exchange.
   Use the patched checkout for acceptance; the seed alone does not avoid token retries.

No image rebuild or PodTemplate apply is needed: existing read-only download mounts
serve these seeds. Re-run seeding after approved-main image digest changes; without
that refresh newly pinned blobs still depend on registry availability.

The node image's existing `test-download-cache.py` runtime acceptance repeats the
same downloader path in two fresh Bazel outputs with repository caching disabled,
a digest-named OCI URL, a read-only seed, and a token endpoint returning 429
before the blob lookup, with failure tolerated only through the same repository
environment variable rules_oci uses. The blob origin also returns 429 if called.
`ci/bazel-job.test.mjs` checks the real index builder and read-only source behavior;
`ci/bazel.test.mjs` checks that the full job and role select it while retaining the
storage-saving flags. The runtime probe downloads only a bounded localhost payload.

`//:node_ci_bazel` also runs `ci/oci-token-probe.py`: the patch's token helper
is executed verbatim with a repository-context adapter and a localhost HTTP stub.
Twenty optional 429 exchanges must take under five seconds with exactly twenty
requests; successful Basic auth and the default Bazel-downloader path are checked.
This bounded unit probe complements the real Bazel distdir runtime acceptance above;
it does not replace the six-image gate-pod acceptance.
