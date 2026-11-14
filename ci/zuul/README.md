# Zuul's configuration

What Zuul runs for AID (#1341, #1346), in two repositories:

- **[kristofk/aid-zuul-config](https://github.com/kristofk/aid-zuul-config)**, the config project (trusted): the
  pipelines, the base job, and the secrets' definitions once #1351 adds them.
- **This folder**, AID's own project (untrusted): the job library, AID's jobs, and which pipeline runs which.

Zuul's deployment, the services that run it, is [`deploy/zuul/`](../../deploy/zuul/README.md). Until Zuul's switch
(#1355), CI stays GitHub Actions plus Mergify, and nothing here gates `main` yet: ThunderBox's tenant takes this layout
once Zuul has its GitHub connection (#1344) and build nodes (#1345) ([On ThunderBox](#on-thunderbox)).

| Path | What | A pull request's change to it |
| --- | --- | --- |
| aid-zuul-config's `zuul.d/` and `playbooks/` | the pipelines, the base job and its playbooks | takes effect once merged |
| [`zuul.d/`](zuul.d) | the job library and AID's jobs, and which pipeline runs which | runs on the pull request itself |
| [`playbooks/`](playbooks) | the library's playbooks and their roles | runs on the pull request itself |
| [`testbed/`](testbed) | Zuul on both, in Docker Compose, with checks | |

## Trusted and untrusted

Zuul splits its configuration in two ([Zuul's docs](https://zuul-ci.org/docs/zuul/latest/tenants.html)), and AID keeps
the halves in separate repositories (Kristóf, on #1346): the trusted half is the security boundary, since what is in it
decides what every job may do and see.

- **The config project is trusted.** Only it may define pipelines, a job with no parent (the base job) and the
  providers of build nodes. Its playbooks run outside the sandbox that untrusted playbooks get, and see the secrets it
  defines. So Zuul never runs a proposed change to it: a pull request there is checked for configuration errors, and
  takes effect once it is merged. It has merge controls of its own: `main` is protected, a change is a pull request
  reviewed by the other bot, and only the orchestrator merges.
- **AID's project is untrusted.** Its jobs, playbooks and roles are tested *speculatively*: a pull request that adds
  or changes a job runs that job, as changed, on itself, merged onto `main` and onto the pull requests ahead of it in
  the gate. What it may not do, Zuul refuses with a configuration error on the pull request: a pipeline, or a base job
  (the test bed checks both).

AID's configuration is in `ci/zuul/zuul.d/`, which the tenant names (`extra-config-paths`), so that all of CI stays
under `ci/`. Zuul would also read a `zuul.yaml`, `zuul.d/`, `.zuul.yaml` or `.zuul.d/` at the repository's root, so
[`check.mjs`](check.mjs) refuses them: AID's jobs have one place.

## The job library

The base job checks the code out; AID's jobs build and test with Bazel, or run a script from the repository. Nothing
in Ansible holds build logic, so that a step runs the same outside Zuul, and Zuul could be replaced without rewriting
the steps (#1341):

- **`base`** (aid-zuul-config's `zuul.d/jobs.yaml`), every job's parent: one build node of the label `aid-node`
  (#1345), a 2-hour timeout, and a pre-run that starts the console stream (`start-zuul-console`) and puts every project
  of the job, as Zuul prepared it, into `~/src/<host>/<owner>/<repository>` (`prepare-workspace-git`). Both roles are
  Zuul's own, from `zuul/zuul-jobs`.
- **`aid-bazel`** ([`zuul.d/jobs.yaml`](zuul.d/jobs.yaml)), abstract: `bazel <bazel_command> <bazel_flags> --
  <bazel_targets>` in the checkout, with the node's Bazelisk, which runs the version `.bazelversion` pins.
- **`aid-script`**, abstract: `./<script> <script_args>` in the checkout, where `script` is a path under `ci/` or
  `scripts/`. The role refuses any other.
- **AID's jobs** set those variables, and nothing else that runs: today `aid-seam-lint`, the seam rule as CI runs it.
  #1347 and #1353 bring the rest of CI's steps, as jobs of the same two kinds.

## Adding or changing a job

An ordinary pull request here, tested by Zuul on itself:

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

They are in kristofk/aid-zuul-config, whose README has the rules: never merge there yourself; a change is a pull
request, reviewed by the other bot and merged by the orchestrator.

1. Run the test bed with the branch ([The test bed](#the-test-bed)): it loads it as the config project, as ThunderBox
   would after the merge, and runs every check.
2. Open the pull request there, with the test bed's run linked. Zuul's `check` on it reports a configuration error, if
   any, before the merge.
3. After the merge, Zuul loads it from `main`. `curl <zuul-web>/api/tenant/aid/config-errors` is empty if it loaded.

A change that needs both repositories (a new pipeline and the jobs in it) is two pull requests. Merge the config
project's first: until it is merged, AID's jobs cannot name the new pipeline.

## The test bed

[`testbed/`](testbed) runs Zuul, with ThunderBox's images, ZooKeeper configuration and `zuul.conf`, on both halves, in
Docker Compose on a GitHub-hosted runner, and checks it with ordinary pull requests
([`testbed/scenario.py`](testbed/scenario.py)):

1. the layout loads: the copy of aid-zuul-config as the config project, and AID's project, with no configuration
   error;
2. a pull request that adds a job (an `aid-bazel` job building a new target) runs it on itself, and reports success
   on the pull request, while `main` still has no such job;
3. a pull request that defines a base job in AID's project fails, with Zuul's "Base jobs may only be defined in
   config projects" on the pull request;
4. a pull request that defines a pipeline in AID's project fails, with Zuul's "Pipelines may not be defined in
   untrusted repos" on the pull request.

Zuul has no route from GitHub there, so the test bed sends Zuul the `pull_request` event GitHub would, signed with the
connection's webhook token. The build node is a container with Bazelisk, a stand-in for #1345's; its provider,
[`testbed/providers.yaml`](testbed/providers.yaml), goes only into the test bed's copy of aid-zuul-config. It replaces
the production `zuul.d/providers.yaml` there: Compose cannot reach the k3s pod. Pipelines, jobs and playbooks are
copied unchanged; the production provider needs a separate live test on k3s.

One thing differs from ThunderBox's Zuul: the token. For every pull request, Zuul's GitHub driver asks for the
repository's branch protection rules, which need the repository's **administration** permission. A workflow's token
cannot have it, and GitHub then answers with no repository at all, which Zuul fails on. So the test bed changes its
copy of that query to leave the rules out ([`testbed/patch.py`](testbed/patch.py)), and Zuul sees an unprotected
branch. ThunderBox's GitHub App (#1344) needs `Administration: read` for the same reason.

It runs in two public repositories of your own, for example `<you>/aid-zuul-testbed` and
`<you>/aid-zuul-config-testbed`, holding copies made by [`testbed/sync.sh`](testbed/sync.sh): of this folder and the
few files Zuul needs, and of an aid-zuul-config checkout. It has no secret: the run makes its keys and passwords, and
Zuul uses the workflow's own `GITHUB_TOKEN`, which reads the public config copy too. The config copy makes
aid-zuul-config's files public, which is safe only while they hold no secret in the clear: a secret there is encrypted
for ThunderBox's project key (#1351), which no test bed has. Once, make both repositories, and
let the test bed's workflows open pull requests:

```sh
gh repo create <you>/aid-zuul-testbed --public
gh repo create <you>/aid-zuul-config-testbed --public
gh api -X PUT repos/<you>/aid-zuul-testbed/actions/permissions/workflow \
  -f default_workflow_permissions=write -F can_approve_pull_request_reviews=true
git clone https://github.com/<you>/aid-zuul-testbed /tmp/aid-zuul-testbed
git clone https://github.com/<you>/aid-zuul-config-testbed /tmp/aid-zuul-config-testbed
```

Each run, from a checkout of this repository's branch and one of aid-zuul-config's (`main`, or the branch to test):

```sh
ci/zuul/testbed/sync.sh /tmp/aid-zuul-testbed /path/to/aid-zuul-config /tmp/aid-zuul-config-testbed
for copy in /tmp/aid-zuul-config-testbed /tmp/aid-zuul-testbed; do   # the config copy first
  git -C "$copy" add -A && git -C "$copy" commit -m "Test bed: $(git rev-parse --short HEAD)"
  git -C "$copy" push --force origin HEAD:main
done
gh workflow run testbed.yml --repo <you>/aid-zuul-testbed
gh run watch --repo <you>/aid-zuul-testbed --exit-status "$(gh run list --repo <you>/aid-zuul-testbed --limit 1 --json databaseId --jq '.[0].databaseId')"
```

The run takes about 4 minutes. It closes its pull requests and deletes their branches; `sync.sh` puts `main` back.
A failed run prints every service's log.

## On ThunderBox

ThunderBox's tenant ([`deploy/zuul/server/config/main.yaml`](../../deploy/zuul/server/config/main.yaml)) holds only
Zuul's job library until #1344 connects GitHub, and installs Zuul's GitHub App on both repositories. Then it takes this
layout, as the test bed's tenant ([`testbed/main.yaml`](testbed/main.yaml)) does, without the test bed's build node:

```yaml
- tenant:
    name: aid
    source:
      github:
        config-projects:
          - kristofk/aid-zuul-config:
              # A config project loads from master unless told (#1342's trial).
              load-branch: main
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

## Checks

- **[`check.mjs`](check.mjs)**, with Node, no packages: the layout's rules. CI's Node tests run it
  ([`../zuul.test.mjs`](../zuul.test.mjs)).
  - no Zuul configuration at the repository's root, and the test bed's tenant reads AID's project from
    `ci/zuul/zuul.d/` and the config copy only for its build node;
  - no pipeline and no base job in AID's project;
  - every playbook a job names exists under `ci/zuul/playbooks/`, and every role a playbook names exists beside it;
  - every job of AID's is `aid-bazel` or `aid-script`, or inherits one of them; every role runs only Ansible's
    `command` and `assert`;
  - the test bed's node has the Bazelisk that `mise.toml` pins.
- **The test bed** ([above](#the-test-bed)), on Zuul itself, for every change to aid-zuul-config or the test bed, and
  to see a change to the library work before Zuul runs on ThunderBox.
