# Zuul's configuration

What Zuul runs for AID (#1341, #1346): its pipelines, its base job, and AID's own jobs. Zuul's deployment, the
services that run it, is [`deploy/zuul/`](../../deploy/zuul/README.md). Until Zuul's switch (#1355), CI stays GitHub
Actions plus Mergify, and nothing here gates `main` yet: ThunderBox's tenant takes this layout once Zuul has its GitHub
connection (#1344) and build nodes (#1345) ([On ThunderBox](#on-thunderbox)).

| Path | Zuul's word | What | A pull request's change to it |
| --- | --- | --- | --- |
| [`config/zuul.d/`](config/zuul.d) | the config project (trusted) | the pipelines, the base job, and the secrets' definitions once #1351 adds them | takes effect once merged |
| [`config/playbooks/`](config/playbooks) | | the base job's playbooks | takes effect once merged |
| [`zuul.d/`](zuul.d) | AID's project (untrusted) | the job library and AID's jobs, and which pipeline runs which | runs on the pull request itself |
| [`playbooks/`](playbooks) | | the library's playbooks and their roles | runs on the pull request itself |
| [`testbed/`](testbed) | | Zuul on this layout in Docker Compose, with checks | |

## Trusted and untrusted

Zuul splits its configuration in two ([Zuul's docs](https://zuul-ci.org/docs/zuul/latest/tenants.html)):

- **The config project is trusted.** Only it may define pipelines, a job with no parent (the base job) and the
  providers of build nodes. Its playbooks run outside the sandbox that untrusted playbooks get, and see the secrets it
  defines. So Zuul never uses a proposed change to it: a pull request that changes `config/` runs with `main`'s
  `config/`, and the change takes effect once it is merged.
- **AID's project is untrusted.** Its jobs, playbooks and roles are tested *speculatively*: a pull request that adds
  or changes a job runs that job, as changed, on itself, merged onto `main` and onto the pull requests ahead of it in
  the gate. What it may not do, Zuul refuses with a configuration error on the pull request, such as defining a
  pipeline (the test bed checks this).

### One repository, two projects

Both halves live in this repository, as AID's code and configuration do (decisions.md, "AID's code and config live in
AIDepartment", 2026-11-13). Zuul takes the repository twice, as two projects with different names:

- **AID's project** comes from the GitHub connection, as `github.com/kristofk/AIDepartment`, and reads only
  `ci/zuul/zuul.d/` (its `extra-config-paths`). Zuul sees its pull requests, tests them and merges them.
- **The config project** comes from a git connection, `aid-config`, with a host name of its own
  (`canonical_hostname=aid-config`) and the owner in its base URL (`https://github.com/kristofk`), as
  `aid-config/AIDepartment`. It reads only `ci/zuul/config/zuul.d/`, and only from `main` (`load-branch: main`), which
  Zuul polls. No pull request reaches it: Zuul learns of a change to it when the merge lands on `main`, at the next
  poll. Its short name, `AIDepartment`, must differ from AID's project's: Zuul 14.3.0 looks a job's playbook up by the
  short name alone, and fails the build when two projects share it (the test bed found it).

Two rules keep the halves apart, and [`check.mjs`](check.mjs) checks both:

- **No Zuul configuration at the repository's root** (`zuul.yaml`, `zuul.d/`, `.zuul.yaml`, `.zuul.d/`). Zuul reads
  those for every project, so the config project would load them as trusted.
- **Neither half reads the other's folder.** `ci/zuul/zuul.d/` and `ci/zuul/config/zuul.d/` are disjoint.

What this costs, against Zuul's usual separate repository for the config project:

- Zuul does not check a change to `config/` before it merges, as it would check a pull request on a config
  repository. Run the test bed on it instead ([Changing the pipelines or the base job](#changing-the-pipelines-or-the-base-job)).
  If a broken `config/` merges anyway, Zuul reports it as a configuration error and keeps what still loads; a broken
  gate pipeline means a fix merged by hand, as when Zuul is down (#1354).
- Whoever may merge into `main` may change the trusted half. With a separate repository the same people would review
  it, so this changes who reviews nothing; #1344's branch protection decides who merges.
- The git connection needs read access to this repository while it is private ([On ThunderBox](#on-thunderbox)).

Moving the config project to a repository of its own later is moving `config/` there and changing the tenant's two
lines.

## The job library

The base job checks the code out; AID's jobs build and test with Bazel, or run a script from the repository. Nothing
in Ansible holds build logic, so that a step runs the same outside Zuul, and Zuul could be replaced without rewriting
the steps (#1341):

- **`base`** ([`config/zuul.d/jobs.yaml`](config/zuul.d/jobs.yaml)), every job's parent: one build node of the label
  `aid-node` (#1345), a 2-hour timeout, and a pre-run that starts the console stream (`start-zuul-console`) and puts
  every project of the job, as Zuul prepared it, into `~/src/<host>/<owner>/<repository>` (`prepare-workspace-git`).
  Both roles are Zuul's own, from `zuul/zuul-jobs`.
- **`aid-bazel`** ([`zuul.d/jobs.yaml`](zuul.d/jobs.yaml)), abstract: `bazel <bazel_command> <bazel_flags> --
  <bazel_targets>` in the checkout, with the node's Bazelisk, which runs the version `.bazelversion` pins.
- **`aid-script`**, abstract: `./<script> <script_args>` in the checkout, where `script` is a path under `ci/` or
  `scripts/`. The role refuses any other.
- **AID's jobs** set those variables, and nothing else that runs: today `aid-seam-lint`, the seam rule as CI runs it.
  #1347 and #1353 bring the rest of CI's steps, as jobs of the same two kinds.

## Adding or changing a job

An ordinary pull request, tested by Zuul on itself:

1. Add the job to [`zuul.d/jobs.yaml`](zuul.d/jobs.yaml), with `parent: aid-bazel` or `parent: aid-script` and its
   variables. What it runs is a Bazel target or a script under `ci/` or `scripts/`, in the same pull request if it is
   new. Set `timeout` when the job needs much less than two hours, and `files` if only some changes should run it.
2. List it under the pipelines that should run it, in [`zuul.d/project.yaml`](zuul.d/project.yaml): `check` for every
   pull request, `gate` for what must pass before a merge.
3. Open the pull request. Zuul's `check` runs the new job on it, as the pull request defines it. A mistake in the
   configuration fails `check` with Zuul's error on the pull request. Fix it and push, or comment `recheck` to run it
   again.

A new playbook or role goes under [`playbooks/`](playbooks), and is tested the same way. Keep it to calling Bazel or a
script: a new kind of step is a script.

`(cd ci/zuul && node check.mjs)` checks the layout's rules before you push ([Checks](#checks)).

## Changing the pipelines or the base job

A change to [`config/`](config) is never tested by the pull request that makes it, so test it first on the test bed,
then merge it:

1. Run the test bed on the branch ([The test bed](#the-test-bed)). It loads the changed `config/` from its own `main`,
   as ThunderBox would after the merge, and runs every check.
2. Say in the pull request that the test bed passed, with the run's link.
3. After the merge, Zuul loads it at the next poll of the config project. `curl <zuul-web>/api/tenant/aid/config-errors`
   is empty if it loaded.

Secrets (#1351) are defined in `config/zuul.d/` too, and used by trusted jobs only.

## The test bed

[`testbed/`](testbed) runs Zuul, with ThunderBox's images, ZooKeeper configuration and `zuul.conf`, on this layout, in
Docker Compose on a GitHub-hosted runner, and checks it with ordinary pull requests
([`testbed/scenario.py`](testbed/scenario.py)):

1. the layout loads: the repository as the config project and as AID's project, with no configuration error;
2. a pull request that adds a job (an `aid-bazel` job building a new target) runs it on itself, and reports success
   on the pull request, while `main` still has no such job;
3. a pull request that changes the base job's trusted playbook to fail still passes: it ran with `main`'s;
4. a pull request that defines a pipeline in AID's project fails, with Zuul's "Pipelines may not be defined in
   untrusted repos" on the pull request;
5. a change to `config/` pushed to `main` takes effect, without a restart, at the next poll.

Zuul has no route from GitHub there, so the test bed sends Zuul the `pull_request` event GitHub would, signed with the
connection's webhook token. The build node is a container with Bazelisk, a stand-in for #1345's; its provider,
[`testbed/zuul.d/providers.yaml`](testbed/zuul.d/providers.yaml), is loaded only by the test bed's tenant.

One thing differs from ThunderBox's Zuul: the token. For every pull request, Zuul's GitHub driver asks for the
repository's branch protection rules, which need the repository's **administration** permission. A workflow's token
cannot have it, and GitHub then answers with no repository at all, which Zuul fails on. So the test bed changes its
copy of that query to leave the rules out ([`testbed/patch.py`](testbed/patch.py)), and Zuul sees an unprotected
branch. ThunderBox's GitHub App (#1344) needs `Administration: read` for the same reason.

It runs in a public repository of your own, for example `<you>/aid-zuul-testbed`, holding a copy of this folder and
the few files Zuul needs, made by [`testbed/sync.sh`](testbed/sync.sh). It has no secret: the run makes its keys and
passwords, and Zuul uses the workflow's own `GITHUB_TOKEN`. Once, make the repository, and let its workflows open pull
requests:

```sh
gh repo create <you>/aid-zuul-testbed --public
gh api -X PUT repos/<you>/aid-zuul-testbed/actions/permissions/workflow \
  -f default_workflow_permissions=write -F can_approve_pull_request_reviews=true
git clone https://github.com/<you>/aid-zuul-testbed /tmp/aid-zuul-testbed
```

Each run, from a checkout of the branch to test:

```sh
ci/zuul/testbed/sync.sh /tmp/aid-zuul-testbed
git -C /tmp/aid-zuul-testbed add -A && git -C /tmp/aid-zuul-testbed commit -m "Test bed: $(git rev-parse --short HEAD)"
git -C /tmp/aid-zuul-testbed push --force origin HEAD:main
gh workflow run testbed.yml --repo <you>/aid-zuul-testbed
gh run watch --repo <you>/aid-zuul-testbed --exit-status "$(gh run list --repo <you>/aid-zuul-testbed --limit 1 --json databaseId --jq '.[0].databaseId')"
```

The run takes about 10 minutes. It closes its pull requests and deletes their branches; `sync.sh` puts `main` back.
A failed run prints every service's log.

## On ThunderBox

ThunderBox's tenant ([`deploy/zuul/server/config/main.yaml`](../../deploy/zuul/server/config/main.yaml)) holds only
Zuul's job library until #1344 connects GitHub. Then it takes this layout, as the test bed's tenant
([`testbed/main.yaml`](testbed/main.yaml)) does, without the test bed's build node:

```yaml
- tenant:
    name: aid
    source:
      aid-config:
        config-projects:
          - AIDepartment:
              load-branch: main
              extra-config-paths: [ci/zuul/config/zuul.d/]
      github:
        untrusted-projects:
          - kristofk/AIDepartment:
              extra-config-paths: [ci/zuul/zuul.d/]
              # Only main's configuration, not every pushed branch's (#1344's trust model).
              exclude-unprotected-branches: true
      opendev.org:
        untrusted-projects:
          - zuul/zuul-jobs:
              include: [job]
```

and `zuul.conf` the config project's connection, beside #1344's `[connection "github"]`:

```ini
[connection "aid-config"]
driver=git
baseurl=https://github.com/kristofk
canonical_hostname=aid-config
poll_delay=60
```

While the repository is private, the git connection needs read access. A token in `baseurl` would show in Zuul's API
(`/api/connections`), so the way is SSH: `baseurl=ssh://git@github.com/kristofk`, with a read-only deploy key in the
scheduler's and the executor's `~/.ssh` (#1344, #1351). Once the repository is public, `https://github.com/kristofk`
needs nothing.

## Checks

- **[`check.mjs`](check.mjs)**, with Node, no packages: the layout's rules. CI's Node tests run it
  ([`../zuul.test.mjs`](../zuul.test.mjs)).
  - no Zuul configuration at the repository's root, and the two halves' folders disjoint, in the test bed's tenant
    too;
  - every playbook a job names exists, under its own half, and every role a playbook names exists beside it;
  - every job of AID's is `aid-bazel` or `aid-script`, or inherits one of them; every role runs only Ansible's
    `command` and `assert`;
  - the test bed's node has the Bazelisk that `mise.toml` pins.
- **The test bed** ([above](#the-test-bed)), on Zuul itself, for every change to `config/` or the test bed, and to
  see a change to the library work before Zuul runs on ThunderBox.
