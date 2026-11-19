# Zuul: what a forge change would take

Zuul is AID's CI so that the forge can change while CI stays (#1341, #1356). This page lists what moving from GitHub to
another forge would touch, with Zuul staying, and what one merge request through Zuul's check and gate on GitLab showed
([The check on GitLab](#the-check-on-gitlab)). It was written on 2026-11-15 and brought up to date on 2026-11-17,
against Zuul 14.3.0 (ThunderBox's, `deploy/zuul/server/version/`), aid-zuul-config's `main` at `62e64c2`, and this
repository's `main` at `e2afc25b`.

**Where CI stands (2026-11-17).**

- **Zuul is the only merge path** since #1355's switch, on 2026-11-16 at 09:44Z ([`cutover.md`](cutover.md)).
  - The ruleset "Only Zuul updates main" lets only the App `aid-zuul` and the administrator's documented exception
    update `main`, and requires `aid/check` and `aid/gate` from that App (`scripts/protect-main.sh`).
  - Mergify is uninstalled, and `.mergify.yml` is gone.
  - No Actions workflow builds, tests, gates, publishes or merges AID (`.github/workflows/README.md`).
- **Every job gets a fresh pod** in check, gate and post. The launcher creates it from a template in the job's
  namespace and deletes it afterwards (#1698, [`disposable-nodes.md`](disposable-nodes.md)).
- **The build farm is two 4 CPU / 6 GiB slots** (#1711, `deploy/build-farm/README.md`).
- **Images are still built by the image jobs' BuildKit**, as #1650 set up (`aid-publish-images` in post, and gate's
  image checks). #1701, which builds them with Bazel's `rules_oci`, was not merged on 2026-11-17.

None of those three is the forge's: the launcher's pods, the farm and the image jobs move with Zuul as they are. What
the switch changed for a forge move is the stakes. GitHub is no longer only where code lives: through Zuul's App, its
webhook and `main`'s ruleset, it is CI's only route to `main`, and no Actions or Mergify fallback is left
(`cutover.md` keeps the undo).

**In short.** The steps don't change: every job runs Bazel or a script under `ci/` or `scripts/` in
`{{ zuul.project.src_dir }}`, and the base job, the disposable job pods, the build farm, the logs and the MQTT
reports are the forge's business nowhere. Everything at the edges does: Zuul's connection and its credentials, the pipelines' triggers and reporters,
the loop's skills and the bots' tools (all `gh`), AID's GitHub app and its events, and the release index's hosting and
releases. The hardest part is not Zuul's: it is the review rules GitHub enforces today and the next forge may not
(below, "What the forge enforces").

## Which forges Zuul can drive

Zuul 14.3.0's drivers for a forge are `github`, `gitlab`, `gerrit`, `pagure` and the plain `git` (`zuul/driver/`).

- **GitLab**: a driver swap, checked here.
- **Forgejo and Gitea**: no driver in Zuul 14.3.0. Until a release has one, Forgejo means writing or carrying a driver, or a plain `git` connection, which can poll branches for
  `post` and `periodic`, but has no merge requests, so no check or gate.
- **Tangled**: no driver, and none proposed. It is on the watch list (#1341), not usable today.

## 1. Zuul's connection and driver

| What | Where | On another forge |
| --- | --- | --- |
| The connection `github`: the App `aid-zuul`'s ID, key and webhook secret | `deploy/zuul/server/config/zuul.conf` | `[connection "<forge>"]` with that driver: for GitLab `server`, `baseurl`, an API token of a bot user that is a Maintainer, `api_token_name` for cloning, and `webhook_token` ([`testbed/gitlab/zuul.conf`](testbed/gitlab/zuul.conf)) |
| The Secret `zuul-github`, its variables `ZUUL_GITHUB_*` and the mount `/etc/zuul-github` | `deploy/zuul/server/{scheduler,web,launcher}.yaml`, `deploy/zuul/executor/executor.yaml`, `deploy/zuul/secrets.sh github`, `deploy/zuul/check.sh` | a Secret with the forge's token and webhook secret; `secrets.sh` gets a subcommand for it, `check.sh` checks it |
| The tenant's `source: github:` and project names `kristofk/…` | `deploy/zuul/server/config/main.yaml`, [`testbed/main.yaml`](testbed/main.yaml) | `source: <connection>:` with the forge's paths (GitLab: `group/project`). `exclude-unprotected-branches` works on GitLab (its branches API says `protected`) |
| The App's permissions, events and webhook; the trust model by permission | [`deploy/zuul/github.md`](../../deploy/zuul/github.md) | rewritten: GitLab's webhook is per project (or group), and checks a plain `X-Gitlab-Token`, not an HMAC signature |
| The webhook route `/api/connection/github/payload`, the Funnel and the forwarder | `deploy/thunderbox/zuul.md` steps 10–17, `deploy/zuul/web.md`, `deploy/zuul/README.md`, `deploy/zuul/namespace/networkpolicy.yaml`, `ci/zuul/independence.mjs` | the same route with the new connection's name in place of `github`; the network path stays. Recovering lost deliveries ("Recovering failed GitHub deliveries") becomes the forge's own redelivery, with its own limits. GitLab also disables a hook that keeps failing |
| The metrics named `github_com` | `deploy/zuul/check.sh`, `deploy/zuul/gatus-*.json` | the new canonical hostname's |
| AIDepartment's `main`: the ruleset "Only Zuul updates main" (bypass: the App `aid-zuul`, 5746770, and the admin role), `aid/check` and `aid/gate` required from that App, one approval, stale approvals dismissed, linear history | `scripts/protect-main.sh`, [`cutover.md`](cutover.md), [`deploy/zuul/github.md`](../../deploy/zuul/github.md), "Branch protection" | the forge's protected branch with only Zuul's user allowed to merge or push. GitLab has no required status from a named integration, so "only Zuul merges" is the protection itself. Its approval rules are the paid-tier settings in [What the forge enforces](#what-the-forge-enforces). The cutover's undo is GitHub's API, step by step |
| aid-zuul-config's protection: `main` requires `aid/check` from the App's integration | `deploy/zuul/protect-config.sh` | the forge's protected branches. GitLab has no required status from a named integration (its "pipelines must succeed" is GitLab CI's); Zuul's merge rights are what protects it |
| Canonical names in secrets and OIDC: `secret_projects: [github.com/kristofk/AIDepartment]`, the pusher's subject `secret:aid/github.com/kristofk/aid-zuul-config/aid-pusher` | aid-zuul-config `zuul.d/release.yaml` and its tests, [`publish.md`](publish.md), `deploy/zuul/post-node/README.md`, ThunderBox's pusher | the new host's. **Zuul keys a project key by its canonical name**: new names mean new project keys, so every secret in aid-zuul-config is encrypted again (`deploy/zuul/encrypt-secret.sh`), and the backup `zuul-project-keys` is taken again |
| The job nodes: the launcher's fixed-namespace Kubernetes adapter, the connections `aid-check-pods`, `aid-gate-pods` and `aid-post-pods`, and each namespace's `PodTemplate/job-node` (#1698) | `deploy/zuul/server/launcher/`, `deploy/zuul/apply-disposable.sh`, aid-zuul-config `zuul.d/providers.yaml`, [`disposable-nodes.md`](disposable-nodes.md) | nothing: no forge in them. Only the projects' canonical names reach a pod, as the workspace path `~/src/<host>/...` |
| The test bed: GitHub's API, its signed deliveries, its GraphQL patch | [`testbed/`](testbed): `scenario.py`, `patch.py`, `check-github-gate.py`, `sync.sh`, `run.sh`, `workflow.yml` | a test bed per forge: [`testbed/gitlab/`](testbed/gitlab) is GitLab's. `patch.py` and `check-github-gate.py` are GitHub-only. GitHub's Actions are only the machine both run on |

## 2. The triggers, and the loop's commands

### Triggers and reporters

aid-zuul-config's `zuul.d/pipelines.yaml` is where the forge is named most: every trigger, requirement and reporter of
`check`, `check-external`, `gate`, `hold` and `post`. Their GitLab form is
[`testbed/gitlab/pipelines.yaml`](testbed/gitlab/pipelines.yaml), which the GitLab test bed swaps in section by section
([`translate.py`](testbed/gitlab/translate.py)); the managers, the window, `supercedes`, `post-review`, the
`aid-events` reporters and the timer pipelines stay as they are.

| GitHub today | GitLab, Zuul 14.3.0 |
| --- | --- |
| `permission: write` on check's and check-external's triggers: only an account with write access starts check (`deploy/zuul/github.md`, "Who makes Zuul run what") | none: GitLab's trigger has no permission filter. Whoever may open a merge request starts check, so the trust model moves to the forge: no outside merge requests into the project, or check without the farm for them |
| check runs `aid/check`, `aid/check-external`, `aid/gate` (only the App may write them) | no status or check run: GitLab's reporter comments, approves, labels and merges. check's result is the label `check-passed`: check takes it off as it starts, puts it on when it passes |
| gate's `status: aid-zuul:aid/check:success` and its `check_run` trigger | `labels: [check-passed]`, and the `labeled` trigger on it. Anyone with Developer access can add that label by hand: a label is not a check only Zuul can set |
| `current-patchset: true` | no such requirement. A push takes the item out of gate (a new patchset), and check takes the label off when it starts, but between the push and that start the old head's label is still on |
| `draft: false`, the `ready_for_review` and `converted_to_draft` events | none: the driver drops a draft's "ready" event and cannot require "not a draft". Ready is the label `ready` |
| `review: approved, permission: write`; `reject: changes_requested` | `approved: true`: GitLab's own `approved` of the merge request. On GitLab Free, with no approval rules, that is one approval by anyone with Developer access. No "request changes" to reject on |
| `label: hold`, `unlabeled` | the same with `labels` and `unlabels` |
| `merge-mode: rebase`, which keeps `main` linear (`zuul.d/project.yaml`, [`gate.md`](gate.md), "Merging") | refused: GitLab's reporter merges with `merge`, `merge-resolve` or `squash-merge`, so `main` gets merge commits. A project set to fast-forward would refuse Zuul's merge of anything not rebased first, which Zuul does not do. The merge call sends no `sha`, so GitLab merges whatever the head is then |
| `push` on `refs/heads/main` (`post`) | `gl_push` on the same ref |
| `Depends-On` across pull requests | GitLab's driver has no reverse lookup yet (`getChangesDependingOn` returns nothing): a merge request can depend on another, but the other isn't found from it |

### What the forge enforces

Two of gate's rules are the forge's today, not Zuul's ([`gate.md`](gate.md), "The two-agent loop"):

- **Nobody approves their own pull request.** GitHub refuses it, so the builder cannot let itself in. GitLab Free
  lets the author approve: the check read `user_can_approve: true` for the builder on its own merge request. "Prevent
  approval by author" is a setting of GitLab's paid tiers.
- **An approval counts for the head it approved.** `main`'s protection dismisses an approval on a push. GitLab Free
  keeps it: after the builder pushed over the head the reviewer had approved, the merge request was still `approved`,
  by `reviewer`, and gate merged the new head on that approval. "Remove all approvals when a commit is added" is a
  setting of GitLab's paid tiers.

So on GitLab Free, as configured in the check, the builder could let itself in, and a push after the review would merge
unreviewed. Before AID gates on a forge that doesn't enforce both, something else must: a paid tier's two settings on
GitLab, or a check of gate's own that the approval is of the current head and not by the author (a job, or a change
to Zuul's driver). The reviewer approving with the head's `sha`, which GitLab's approve call takes and checks, is not
enough: it stops an approval of a stale head, not a push after the approval.

### The loop's commands

The two-agent loop is GitHub's from end to end, in AID's shared skills (`/skills`, not in this repository):

- **`work-on-issue`**: `gh pr create --draft`, `gh pr edit --add-reviewer` and the `requested_reviewers` calls,
  the GraphQL `reviewThreads` and the review comments' `/replies`, `gh pr ready` and `--undo`, the 👀 reactions and the
  reviews list, the Projects "Status" field, `gh workflow run` and `gh run watch`.
- **`review-pr`**: `gh pr view`, the 👀, `git fetch origin pull/<n>/head` (GitLab: `merge-requests/<n>/head`), the
  `resolveReviewThread` mutation, and the review with `APPROVE` or `REQUEST_CHANGES` and inline comments.
- **`orchestrate-milestone`** and **`event-driven-agent`**: milestones, assignees, `@mergifyio`, the GitHub app's
  events and its deliveries.
- **The docs of the loop**: [`docs/review-loop.md`](../../docs/review-loop.md) (the bots' logins, the branch
  `<bot>-issue-<n>`, the value keys built from GitHub's `repository.id` and `head.ref`, `assigned` and
  `review_requested`), [`docs/development.md`](../../docs/development.md) (`gh`, branch protection, the `hold` label, Zuul's gate), and
  `docs/examples/*/automations/*.json`.

On GitLab each becomes `glab` or its API: merge requests, discussions (resolvable threads), approvals in place of an
approving review, award emoji in place of reactions, and no "request changes". Only the branch name is the loop's own.
GitLab has no Projects "Status" field: issue boards and their labels are the nearest thing.

## 3. AID's events

- **AID's GitHub app** (`apps/github/`) is the loop's event source: its 12 event names in `declaration.json`, the
  signature `X-Hub-Signature-256` and the headers `X-GitHub-Event` and `X-GitHub-Delivery` (`adapter/src/app.mjs`),
  the payload shapes it turns into `payload.aid` (`adapter/src/events.mjs`), the subject `github:<owner>/<repo>#<n>`
  and `api.github.com` (`adapter/src/github.mjs`). Another forge is another app beside it: GitLab sends `X-Gitlab-Event`
  with `object_kind` payloads; Forgejo sends GitHub-like payloads signed with `X-Forgejo-Signature`. If the new app
  fills the same `payload.aid` fields, the bots' automations keep their filters on those.
- **The bots' automations** (in AID's configuration, not this repository) filter on the GitHub app's fields
  (`repository.id`, `pull_request.head.ref`, `requested_reviewer.login`, `assignee.login`). They are rewritten for the
  new app's events, or for `payload.aid`.
- **Swift**: `Packages/AIDRecords/Sources/AIDRecords/Model.swift` finds the repository only for the application
  `github`, from `repository.full_name`. `Packages/AIDBase/Sources/AIDBase/Redaction.swift` knows `github_pat_`, not
  GitLab's `glpat-`. AID's IDs (`<provider>:<owner>/<repo>`) are already neutral.
- **Zuul's own events**, through MQTT to AID's Zuul app (`apps/zuul/`), are the forge's nowhere but in their values:
  `project` is the forge's path, `change` and `patchset` the merge request's number and head, `change_url` the forge's
  link. The GitLab check recorded a gate report ([below](#the-check-on-gitlab)).

## 4. The release index (GitHub Pages)

- **Hosting.** `contracts/application.md`, "The release index", publishes `https://kristofk.github.io/AIDepartment/index.json`,
  served by Pages from the branch `release-index` (#1350). The branch is a whole static site
  (`ci/release-index.mjs` writes it, `.nojekyll` included), so any static host can serve it: GitLab Pages, Codeberg
  Pages, or ThunderBox. What changes is the URL in the contract and in every operator's `settings.releaseIndexUrl`
  (`deploy/thunderbox/README.md`). AID's reader takes any URL. What is GitHub's here goes: Pages' generated
  `pages-build-deployment`, the one Actions workflow left, which Pages runs to serve the branch
  (`.github/workflows/README.md`), and the Pages API calls in `ci/release-index.mjs`.
- **Releases.** AID's release is a GitHub Release with the assets `aid-release.json` and `aid-keychain-linux-amd64`
  (`contracts/application.md`, `contracts/platform.md`): `ci/aid-release.mjs`, `ci/release-app.mjs`,
  `ci/github-rest.mjs` and `ci/zuul/publish/github.mjs` call `api.github.com` and `uploads.github.com`;
  `ci/zuul/publish/job-env.sh` makes `GITHUB_*` variables, the committer `aid-release[bot]` and the origin
  `https://github.com/kristofk/AIDepartment.git`. On GitLab: its Releases API with generic packages (or release links) for
  the assets.
- **Issues on failure.** The release and the nightly open an issue (`ci/aid-release.mjs`, `ci/nightly.mjs`) through the
  App `aid-release`, whose ID and key are aid-zuul-config's secrets (`zuul.d/secrets.yaml`,
  `playbooks/release/credentials.yaml`, `github-app-token.py`). On GitLab, a project access token in their place.

## 5. The bots' tools

- **`gh`**: every builder and reviewer step above, installed and signed in by the box (`apps/t3codebox/Dockerfile`,
  its credential helper for `github.com`, `adapter/src/runner/accounts.mjs` reading `gh`'s `hosts.yml`,
  `rootfs/etc/mise/config.toml`). On GitLab, `glab` and a credential helper for its host.
- **AID's GitHub tools** (`github-<instance>_comment`, `get-issue`, `get-pull-request`, `list-comments`) are AID's
  GitHub app (`apps/github/adapter/src/tools.mjs`), fixed to `api.github.com` by design (`apps/github/README.md`). A new
  forge is a new app, or a host setting on this one.
- **The bots' accounts**: `Opifex-Magnus` and `Artifex-Prime` on GitHub, with tokens the boxes hold, and the
  orchestrator's keychain items for Zuul's Apps (`zuul-github-app-key`, `zuul-github-webhook-token`,
  `zuul-release-app-key`). Each needs its counterpart on the new forge.
- **T3 Code's pull request tools** take a `host`; which forges they follow is T3 Code's question.

## Also GitHub's, outside Zuul

These go with GitHub whichever CI runs. #1355 already removed the biggest: the Actions workflows that built, gated
and published, `.mergify.yml` and Mergify, and the image workflows, whose images post's `aid-publish-images` now
publishes (#1650). What is left:

- `.github/`: its `README.md`, the pull request template and `actionlint.yaml`.
- The build farm still names GitHub Actions' OIDC issuer, as retired: it refuses Actions tokens, and the issuer
  stays only until the operator removes its JWKS mount (`deploy/build-farm/server/config/clients.libsonnet`,
  `deploy/build-farm/server/github-jwks.yaml`, `scripts/build-farm-credential.sh`). Zuul's pods use their
  Kubernetes service account tokens, which no forge issues.
- `arc-on-nas` as a remote kustomize base (`deploy/zuul/namespace/kustomization.yaml`), and the OCI `image.source`
  labels.
- `CLAUDE.md` ("Tasks are GitHub issues", `.github/`'s precedence), `README.md`, `CONTRIBUTING.md`.
- Links to issues and pull requests in `docs/decisions.md` and `ci/zuul/comparison*`: history, which needs the old
  forge kept readable or archived, not rewriting.

## The check on GitLab

[`testbed/gitlab/`](testbed/gitlab) runs Zuul, with ThunderBox's images, ZooKeeper configuration and `zuul.conf`, against
GitLab CE in Docker Compose, and takes merge requests through check and gate as the two-agent loop's would. It has no
secret and no account anywhere: the run makes GitLab, its users (`builder`, `reviewer`, and `zuul`, a Maintainer)
and their tokens.

- **What GitLab gets**: the GitHub test bed's two copies ([`testbed/sync.sh`](testbed/sync.sh)): AID's `ci/zuul/` with
  its stand-in scripts and Bazel workspace, and aid-zuul-config's base job and playbooks, without its secrets. Two
  changes, both on the copies only: the pipelines become GitLab's
  ([`translate.py`](testbed/gitlab/translate.py) from aid-zuul-config's own `pipelines.yaml`), and `merge-mode: rebase`
  becomes `merge`.
- **What runs** ([`scenario.py`](testbed/gitlab/scenario.py)):
  1. the tenant loads both projects from GitLab, with no configuration error;
  2. the builder's merge request passes check (check's jobs run, the label `check-passed` goes on);
  3. labelled `ready`, but not approved, it stays out of gate;
  4. the reviewer's approval lets it in: gate runs `aid-bazel-everything` on it, and Zuul merges it;
  5. `hold` keeps a ready, approved merge request out. Then the builder pushes, check passes again, and `hold` comes
     off: whether the approval of the older head still lets it in is recorded.

  It also records whether GitLab lets the author approve its own merge request, and whether `main`'s head after the
  merge is a merge commit.

### Seen

[Run 43775011585](https://github.com/Artifex-Prime/aid-zuul-gitlab-testbed/actions/runs/43775011585), 2026-11-15
23:28–23:40Z, of this branch's `c5a2d00e` with aid-zuul-config `a7374117`, GitLab CE 19.5.5 and Zuul 14.3.0. It passed
checks 1 to 5:

1. The tenant loaded both projects from GitLab with no configuration error, with the pipelines `check`,
   `check-external`, `gate`, `hold` and `post`.
2. !1 passed check on `7031bd242728`: `aid-apps-lint`, `aid-apps-lint-test`, `aid-seam-lint` and `aid-seam-lint-test`
   ran, and Zuul labelled it `check-passed`.
3. Labelled `ready`, checked, but not approved, !1 stayed out of gate for a minute.
4. The reviewer's approval let it in. gate ran `aid-bazel-everything`, and Zuul merged it as `1eb9628405ff`, from
   opening to merge in 2 minutes 14 seconds.
5. `hold` kept !2 out of gate, ready, checked and approved.

Rerun after the rebase onto 2026-11-17's `main` (`e2afc25b`, aid-zuul-config `62e64c2`): [run
44176369633](https://github.com/Artifex-Prime/aid-zuul-gitlab-testbed/actions/runs/44176369633), 21:07–21:18Z, of
`73f45bde`, passed the same five checks. The layout of that day loaded from GitLab, with the periodic pipelines and
`aid-cheap-checks` in check, and every observation below came out the same.

What it recorded:

| Observation | Seen | So |
| --- | --- | --- |
| May the author approve its own merge request (`user_can_approve`, read as the builder) | `true` | the builder could let itself in ([What the forge enforces](#what-the-forge-enforces)) |
| Is the merge request still approved after a push over the approved head | `true`, by `reviewer` | the approval doesn't belong to a head |
| With `hold` off, did gate merge !2's new head `e5d4626b03dc` on the approval of `540e2abdd803` | `true` | a push after the review merged unreviewed |
| Parents of `main`'s head after Zuul merged | 2 | a merge commit: `main` is not linear on GitLab ([Triggers and reporters](#triggers-and-reporters)) |
| The first gate report over MQTT | `project` `aid/aidepartment`, `change` 1, `patchset` its head, `ref` `refs/merge-requests/1/head`, `change_url` `http://gitlab/aid/aidepartment/merge_requests/1`, `owner` `builder` | AID's Zuul app gets the same fields, with GitLab's values ([AID's events](#3-aids-events)) |

Two fixes it needed, both on the test bed's side: GitLab 19 doesn't protect `main` on the first push, so `scenario.py`
protects it; and the base job's log store must be writable from the executor's bubblewrap, whose user is not the
runner's (`run.sh` makes it `1777`). Without that, every build ends `POST_FAILURE`, which is what the GitHub test bed
shows in #1540.

### Running it

In a public GitHub repository of your own (its Actions are only the machine; about 30 minutes, most of it GitLab
starting), from a checkout of this repository's branch and one of aid-zuul-config's:

```sh
gh repo create <you>/aid-zuul-gitlab-testbed --public
git clone https://github.com/<you>/aid-zuul-gitlab-testbed /tmp/aid-zuul-gitlab-testbed
ci/zuul/testbed/gitlab/sync.sh /tmp/aid-zuul-gitlab-testbed /path/to/aid-zuul-config
git -C /tmp/aid-zuul-gitlab-testbed add -A && git -C /tmp/aid-zuul-gitlab-testbed commit -m "GitLab test bed: $(git rev-parse --short HEAD)"
git -C /tmp/aid-zuul-gitlab-testbed push origin HEAD:main
gh workflow run gitlab.yml --repo <you>/aid-zuul-gitlab-testbed
```

The run uploads `gitlab-results.json`: each check that passed, each observation, and the first gate report Zuul sent
over MQTT.
