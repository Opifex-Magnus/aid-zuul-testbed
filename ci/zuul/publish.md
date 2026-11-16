# Publishing from main on Zuul

Zuul's trusted `post` jobs publish app images, AID releases and the release index from main;
its periodic jobs run the nightly. The job definitions and secrets live in `kristofk/aid-zuul-config`;
repository entrypoints are under [`publish/`](publish). This document describes the target after
#1355's [held cutover](cutover.md). The existing turn-on procedure below remains **pending #1350
through step 7**; do not remove the hold until its live publishing evidence is recorded.


## The pipelines, and who starts them

| Pipeline | Starts on | Who can start it | Post-review | Its jobs |
| --- | --- | --- | --- | --- |
| `post` | a push to `main` | Zuul's `gate`, or the documented administrator state/recovery exception. `main` is protected, so nobody else | yes | `aid-release-credentials` (#1351), `aid-publish-apps`, `aid-release` |
| `periodic-0200`, `periodic-0300` | a timer, at 02:00 UTC and at 03:00 UTC | nobody: Zuul's clock, on `main`'s head | yes | `aid-nightly` (this repository's), `aid-nightly-report` |

Both are aid-zuul-config's (`zuul.d/pipelines.yaml`), and post-review, so the only pipelines where a job with a secret
may run. Their items are refs, never a pull request, which the secrets' guard checks again.

**No tag pipeline.** AID releases start from a VERSION change on main and create `aid-v<version>`
when published. The unused app tag attachment jobs retire with Actions; no `<app>-v*` tag has ever
been published (Kristóf's #1350 decision). App releases are discovered from their indexed declarations.

**No reporter.** Neither pipeline has a pull request to report on. A failed release opens its issue (`aid-release`'s
post-run, as `aid-release.yml`'s report job does). The nightly's result goes to its `ci-nightly` issue
(`aid-nightly-report`). Every build is in Zuul's API and web UI (`/api/tenant/aid/builds?pipeline=post`). AID's events
from Zuul are #1526's.

## The jobs

Each publishing job runs on the **post node** ([`deploy/zuul/post-node/`](../../deploy/zuul/post-node/README.md)),
which only these jobs name: no pull request's job runs there, and nothing there is the PR node's. Its stages are
playbooks of their own, and each stage gets only the credentials it uses, made as it starts
(aid-zuul-config's `playbooks/publish/stage.yaml`):

| Stage | App images (`aid-publish-apps`, [`publish/apps.sh`](publish/apps.sh)) | AID's release (`aid-release`, [`publish/release.sh`](publish/release.sh)) | Credentials |
| --- | --- | --- | --- |
| `build` | for every app whose payload the push changed (`ci/image-policy.mjs`): the image built with BuildKit, checked as AID starts it, its declaration written | the plan (`VERSION` changed, unreleased, the changelog dated); the release folder built clean (`deploy/release-folder.sh`, #1298); the three images built and checked; the keychain asset and the entry written | the App's token, `contents: read`, for the push's base and the tags |
| `push` | each image through ThunderBox's pusher | the version still unreleased, then the three images through the pusher | Zuul's token for the pusher; the release also the App's, `contents: read` |
| `record` | each pushed image's declaration into the release index | the GitHub Release `aid-v<version>`, then its entry into the index | the App's token, `contents: write` |
| `report` | | after a failure only: the release's issue | the App's token, `issues: write` |

What a stage hands to the next is in `ZUUL_STATE` on the node: the plan, the images by digest, the declarations and
the entry, as files. Nothing goes through a forge's artifacts (#1286). Zuul runs a job's next playbook only after one
that passed, so an app whose push fails does not fail `push`: `record` indexes every app that did publish, then fails
the job for it. A failure anywhere in `aid-release` opens the release's issue, a refusal by the plan included, unless
the plan ran to its end and released nothing.

**Not carried over from Actions:** the merge queue's checked images (#466). Zuul's gate builds and checks each image
a pull request changes, with the same commands ([`steps.sh`](steps.sh), [`gate.md`](gate.md), "The gate's jobs"), but
keeps none: a speculative state's image is never published. So `post` builds and checks each image once more itself,
as Actions does without a queue image.

[`publish/job-env.sh`](publish/job-env.sh) is the one place that maps Zuul's variables onto those the release scripts
read, which GitHub Actions names: the push's range (`GITHUB_SHA`, the event's `before`), the run (`GITHUB_RUN_ID` is the
build's UUID, which names the scratch images and labels the image-check pods), `CI_SYSTEM=zuul`, and `origin` with the
App's token, read by git from its file.

The job's timeout is the tenant's maximum, two hours, for all its stages: each copy through the pusher may take 15
minutes (`CI_PUBLISH_TIMEOUT_SECONDS`), where Actions allows an hour.

### The nightly

`aid-nightly` is this repository's job ([`zuul.d/jobs.yaml`](zuul.d/jobs.yaml)), untrusted and with no secret, on the
PR node with the farm's `dev` credential: [`publish/nightly.sh`](publish/nightly.sh) builds and tests everything with
a BUILD file with no cached test result, on `main`'s head. Zuul's timer has no time zone, so it fires at 02:00 UTC
(`periodic-0200`) and at 03:00 UTC (`periodic-0300`), and `ci/nightly.mjs`'s clock lets through only the one that is
04:00 in Madrid (the owner's decision of 2026-10-20, #225), and only while `ci/publisher` names Zuul. Otherwise it
passes having run nothing. A pipeline per time because Zuul tells a job its pipeline, not when its timer fired: an
item that waits for the PR node past the other time keeps the decision of the timer that made it, in both jobs.

That is the Bazel part of Actions' nightly. The rest of CI's jobs are check's (the Node suites, #1520) and gate's (the
images, the ingress suite and the rest, #1521, [`gate.md`](gate.md), "The gate's jobs"); the nightly does not run them
yet.

`aid-nightly-report` (aid-zuul-config's `playbooks/nightly/`) runs beside it on the executor, with no node. It decides
by the same pipeline's time and `ci/publisher` first, with no credential; only then does it make a token with `issues: write`,
wait for `aid-nightly`'s result in the same buildset from Zuul's API, and close, comment on or open the `ci-nightly`
issue, as `ci/nightly.mjs report` does on Actions. A nightly with no result within the job's two hours is reported as
one.

## The credentials

- **The App `aid-release`** ([`deploy/zuul/job-secrets.md`](../../deploy/zuul/job-secrets.md)): its key is the secret
  `aid-release-github`, and never leaves the executor. Each stage gets a token made for it with only its permissions,
  for AIDepartment alone, revoked when the stage ends. The App needs **Contents: read and write** (it has it) and
  **Issues: read and write**, for the release's and the nightly's issues ([Turning it on](#turning-it-on), step 1).
- **The pusher**: Zuul's own OIDC token, the secret `aid-pusher` (no stored value: Zuul signs a token as each stage
  starts, for three hours at most, the tenant's maximum). Only the `push` stage hands it to the script.

### The pusher

ThunderBox's pusher, the registry's one writer, accepts GitHub's OIDC token today. For Zuul it accepts one more issuer,
and only this token of it (`ci/publish-image.mjs` sends it as the bearer, from `CI_PUBLISH_TOKEN_FILE`):

| Claim | Value |
| --- | --- |
| `iss` | `http://web.zuul.svc.cluster.local:9000`, zuul-web in the cluster, with its keys at `<iss>/oidc/jwks`: set on the secret (`iss`), and allowed by the tenant (`allowed-oidc-issuers`, #1637). Zuul's default would be `[web] root`, AID's Zuul UI's address, which the pusher neither trusts nor reaches |
| `aud` | `thunderbox-registry`, as GitHub's tokens have it (`CI_PUBLISH_AUDIENCE`) |
| `sub` | `secret:aid/github.com/kristofk/aid-zuul-config/aid-pusher`: the tenant, the config project and the secret. No pull request can name that secret (Zuul: "Secrets must be defined in the same project in which they are used") |
| `pipeline` | `post` |
| `job-name` | `aid-publish-apps` or `aid-release` |
| `exp` | at most three hours after `iat` |

The test bed's check 11 has Zuul 14.3.0 sign such a token in `post` and checks each claim, and its signature against
Zuul's published keys ([`README.md`](README.md), "The test bed"). The change to the pusher is ThunderBox's (its
repository holds the pusher).

**Where it fetches the keys.** In the cluster, from Zuul's web: `http://web.zuul.svc.cluster.local:9000/oidc/jwks`.
The namespace `zuul` lets in only what its policies name ([`deploy/zuul/namespace/networkpolicy.yaml`](../../deploy/zuul/namespace/networkpolicy.yaml)),
so AIDepartment's policy `web-pusher` lets the pusher, the pod `pusher` in `arc-release`, reach `web`'s port 9000, and
no other pod of another namespace (#1601). The pusher's egress to it is ThunderBox's. Without both, every `push` stage
is refused with 401: the pusher cannot fetch the keys, so the token's key is not one of the issuer's. ThunderBox also lets the post node reach the API for the image check and the pusher
([`deploy/zuul/post-node/README.md`](../../deploy/zuul/post-node/README.md), "What ThunderBox grants"). Its BuildKit is
its own, which only it reaches ([`deploy/zuul/post-buildkit/`](../../deploy/zuul/post-buildkit/README.md), #1566).

## The release index

The index branch `release-index` now holds the whole Pages site in each commit: `index.json`, the page that asks
crawlers not to list it, `robots.txt`, and `.nojekyll` (`ci/release-index.mjs`, `siteFiles`). So GitHub Pages can serve
the branch as it is, and no job deploys it: GitHub's Pages deployment API takes only an Actions artifact and an Actions
OIDC token, which no other CI has. The address stays `https://kristofk.github.io/AIDepartment/index.json`.

Pages then builds the branch itself, with its own workflow (`pages-build-deployment`) running from `release-index`,
in the `github-pages` environment, whose branch rule must allow `release-index` (step 5). What starts that build:

- **A record by Zuul's jobs**: their push, with the App's token, starts it.
The App's push starts GitHub's generated `pages-build-deployment` workflow on `release-index`.
That GitHub-owned workflow is the one automation that stays: Pages needs it to serve the branch.
There is no checked-in workflow YAML and no Actions artifact handover. The old dispatch/deploy
workflow is removed only after #1350 step 5 proves branch publication works.

One thing changes: a merge that changes how the site is written (`ci/release-index.mjs`) shows on Pages at the next
record, not at once (#972's re-deploy).

## Which CI publishes

`ci/publisher` names `zuul`. Keep this fail-closed guard while the orchestrator completes the
cutover: trusted publishing and nightly jobs refuse to run if it names another system. No
Actions publishing workflows remain after this switch. Changing the marker alone is therefore
not a rollback after #1355: restore the pre-cutover workflows/settings as described in [cutover.md](cutover.md).

## Turning it on

The pre-cutover procedure owned by #1350. **Pending #1350 through step 7.** It runs before this
workflow-removal PR merges, from the pre-cutover checkout, which still has its transition workflows.
In order, each step only once the one before is done. Each says whose it is.

0. **The post node's memory** (Kristóf's decision): ThunderBox's documented node budget is full (14336 of 14336 MiB),
   and the post node requests 128 MiB more. Raise the node's cap by that, or give it up elsewhere
   ([`deploy/zuul/post-node/README.md`](../../deploy/zuul/post-node/README.md), "Its share of the node").
1. **The App's permission** (Kristóf): in `aid-release`'s settings, add **Issues: read and write**, and accept it on
   the installation. Check: aid-zuul-config's `aid-release-credentials` still passes on the next merge.
2. **The post node** (the admin, after a heads-up to the orchestrator): [`deploy/zuul/post-node/README.md`](../../deploy/zuul/post-node/README.md),
   "Applying it", with `deploy/build-farm/images/admission.yaml` applied first. Then its BuildKit, with its dataset,
   the node's mount and the node's memory cap raised by its 2560 MiB (Kristóf's decision on #1566):
   [`deploy/zuul/post-buildkit/README.md`](../../deploy/zuul/post-buildkit/README.md), "Applying it".
3. **ThunderBox's grants** (ThunderBox's session): the post node's reach and its binding in the image-check namespace,
   the scratch registry admitting the post node's BuildKit, the pusher's acceptance of Zuul's token
   ([The pusher](#the-pusher)), and the endpoints' values. `BUILDKIT_HOST` is the post node's own BuildKit's,
   `tcp://buildkitd.zuul-post-buildkit.svc:1234`.

   With these, the admin applies `zuul`'s namespace for its policy `web-pusher` (#1601), from a checkout of `main` that
   has it: `kubectl kustomize deploy/zuul/namespace | ssh thunderbox 'sudo docker exec -i ci-k3s-1 kubectl apply -f -'`.
   It must be live before step 6, not before step 4: while `ci/publisher` names Actions, the jobs push nothing. Check:
   from the pusher's pod, `wget -qO- http://web.zuul.svc.cluster.local:9000/oidc/jwks` lists Zuul's keys, and from a
   pod in another namespace, `zuul-nodes` for one, the same `wget -T 5` cannot connect.
4. **aid-zuul-config** (the orchestrator merges, after the other bot's review): the post node in `providers.yaml`'s
   provider with its host key, `publish_endpoints` and `zuul_web_root` set in `zuul.d/publish.yaml`, and the project
   stanza that runs its jobs, which that file gives in a comment: until this step no project runs them. Its `check`
   loads them; after the merge `/api/tenant/aid/config-errors` is `[]`, and the next merge into `main` runs
   `aid-publish-apps` and `aid-release`, which publish nothing while `ci/publisher` names Actions. Check: both pass,
   and their `build` stage's log says so.
5. **Pages from the branch** (decided by Kristóf on 2026-11-15, `docs/decisions.md`, with `contracts/application.md`,
   "The release index", saying so), Kristóf's, in this order:
   1. Let the `github-pages` environment deploy from `release-index`, keeping `main`, which `release-index.yml` still
      runs from: `gh api -X POST repos/kristofk/AIDepartment/environments/github-pages/deployment-branch-policies -f
      name=release-index -f type=branch`. Check: `gh api
      repos/kristofk/AIDepartment/environments/github-pages/deployment-branch-policies --jq '[.branch_policies[].name]'`
      lists `main` and `release-index`.
   2. Settings › Pages › Build and deployment: **Deploy from a branch**, `release-index`, `/ (root)`.
   3. Check, before step 6: `gh workflow run release-index.yml --ref main`. Its run says "Pages is asked to build it",
      a `pages-build-deployment` run from `release-index` follows and succeeds (`gh run list --workflow
      pages-build-deployment`), and `curl -sf https://kristofk.github.io/AIDepartment/index.json` gives the branch's
      `index.json`. The branch holds the whole site from its first record after #1550; before that it has
      `index.json` alone, which Pages serves as it is.

   Undo: Settings › Pages back to **GitHub Actions**, then `gh workflow run release-index.yml --ref main`, which
   deploys again.
6. **The switch**: a pull request that sets `ci/publisher` to `zuul`. From its merge, Zuul publishes, and Actions'
   publishing jobs do nothing.
7. **Seen to work** (#1350's "Done when"): an app's change merged, and a patch release (`VERSION` and `CHANGELOG.md`),
   published by Zuul's jobs; the index lists both with the digests the pusher returned, and Pages serves it.

Undo: a pull request setting `ci/publisher` back to `actions`; Actions publishes again from its merge, and its
records reach Pages through `release-index.yml`'s build request ([The release index](#the-release-index)).

## Making a config-project merge live

Until [#1642](https://github.com/kristofk/AIDepartment/issues/1642)'s webhook-path failure is fixed, after **each
merge into `aid-zuul-config`**, explicitly reload tenant `aid` before re-enqueuing publishing or relying on the new
configuration. A successful `check` loads the proposed configuration for that item; it does not prove the merged
tenant layout is live. An empty `/api/tenant/aid/config-errors` alone does not prove freshness either.

From a box with the tenant credential (or the admin, using `ssh thunderbox`):

```sh
export KUBECONFIG=~/.kube/devinfra.yaml
kubectl -n zuul exec deploy/scheduler -- zuul-scheduler tenant-reconfigure aid
kubectl -n zuul logs deploy/scheduler --since=5m
curl -fsS http://zuul-web:9000/api/tenant/aid/config-errors
```

The command submits a reload; wait for its **new** `Reconfiguration complete (smart: False, tenants: ['aid'], …)`
log entry, with the config project's changed `zuul.d/` files loaded from `main`, and check that `config-errors` is
`[]`. Then check the behavior the change intended, such as the next publishing build's pusher acceptance. Do not use
`smart-reconfigure` for this workaround: when `main.yaml` is unchanged it can report `Reconfigured tenants: []` and
leave the repository configuration cached. This reload does not replay a missed `post` push; use the procedure
below for that, after the configuration is live.

The missing reload at 05:48Z on 2026-11-16 was a failed **push delivery**, not evidence that the scheduler lost an
accepted event. GitHub's App delivery `4b091748-e181-11f1-9d77-46213b1fbc68`, for
`aid-zuul-config`'s `main` update `20ab112a…dc048a4c` (PR #19), received HTTP **502** at 05:48:06Z. Funnel logged
`http: proxy error: write tcp …: use of closed network connection`, and the webhook forwarder logged `Broken pipe`
at that second. The two deliveries zuul-web did accept nearby were `pull_request/closed` and `check_suite/requested`;
the push never reached its access log. Another delivery failed the same way at 06:20:43Z, without a web restart.
The 06:05Z tenant reload loaded the change; the 06:04Z smart reload had reconfigured no tenant.

For a missing automatic reload, inspect the App's **push** delivery and its attempts, correlating its GUID and UTC
time with Funnel, forwarder, web and scheduler logs. A nearby successful PR-closure delivery is insufficient.
Follow [Recovering failed GitHub deliveries](../../deploy/thunderbox/zuul.md#recovering-failed-github-deliveries)
to inventory failures on both repositories and redeliver only GUIDs with no successful attempt. A tenant reload
recovers the current configuration, but does not recover other missed events. The investigation on [#1640](https://github.com/kristofk/AIDepartment/issues/1640) redelivered that push once at
06:26:57Z: GitHub recorded HTTP 200 at 06:26:58Z, and the scheduler logged `Tenant reconfiguration complete for aid`
at 06:27:11Z with the same delivery GUID (12.801 seconds), with `config-errors` still `[]`. No scheduler restart or
manual reload was needed for that replay. The remaining ingress repair and other failed deliveries are tracked in
#1642. Keep the explicit reload until the transport fix proves reliable delivery and automatic reload, including
after an idle interval.

## Running a publishing job again

A failed `post` item runs again as an operator enqueues its push once more, through zuul-web's admin API
([`deploy/zuul/web.md`](../../deploy/zuul/web.md), "The admin API", #1616). The orchestrator decides it, and runs it or
has a bot run it; either needs to run a command in web's pod, the tenant's `~/.kube/devinfra.yaml` or the admin's
`ssh thunderbox`. `<before>` and `<after>` are the push's, both full SHAs: the failed buildset's `refs[0].oldrev` and
`refs[0].newrev`, which a box reads with `curl -s 'http://zuul-web:9000/api/tenant/aid/buildsets?pipeline=post&limit=5'
| jq '.[] | {uuid, result, refs}'`.

```sh
export KUBECONFIG=~/.kube/devinfra.yaml
kubectl -n zuul exec -i deploy/web -- sh -s <who> enqueue-ref --tenant aid --pipeline post \
  --project kristofk/AIDepartment --ref refs/heads/main --oldrev <before> --newrev <after> < deploy/zuul/admin-api.sh
```

It prints nothing but zuul-admin's deprecation warning, and exits 0, once Zuul has the event; a new `post` buildset for
`<after>` then shows in `buildsets?pipeline=post`. It runs every `post` job again for that push, not only the failed
one: the credentials, the apps and the release. Each starts from `build`:

- **An app not published** (its `push` failed): the run builds it again and publishes it. An app published and not
  recorded is published again too, under the same version, which the pusher replaces, and recorded.
- **A release not published**: the run builds anew. Once its GitHub Release exists, the plan refuses the version, a tag
  never moving. If only the index lacks it, record its entry by hand, from the release's `aid-release.json`: `node
  ci/aid-index.mjs record aid-release.json <aid digest> <aid-broker digest> <aid-ingress digest>`, each digest the one
  after `@` in the entry's `images`, from a checkout of `main` that can push.
