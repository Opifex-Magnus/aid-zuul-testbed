// The gate's plan (#1521, gate.md, "The gate's jobs"): which of gate's jobs a change needs, from the plan CI's queue
// layer reads (ci/affected.mjs), so that Zuul's gate runs what `Every package passed` requires on GitHub Actions and
// nothing the plan leaves out.
//
//   node ci/zuul/gate.mjs plan <base> <plan.json>   the change against <base> (Zuul's origin/<branch>: the state
//                                                   ahead of it): the plan, and the jobs it names, written to
//                                                   <plan.json>; aid-gate-plan hands that list to Zuul (child_jobs)
//   node ci/zuul/gate.mjs jobs                      every job the plan can name, one per line: gate's list in
//                                                   zuul.d/project.yaml, after aid-gate-plan (check.mjs holds them equal)
//
// Zuul runs a job of gate only when aid-gate-plan names it, and skips the others; a job it names that fails, times out
// or gets no node fails the gate. A plan that cannot list the changed files plans everything, as the queue's does.
import { existsSync, mkdirSync, readdirSync, writeFileSync } from "node:fs";
import { dirname, posix } from "node:path";
import { fileURLToPath } from "node:url";
import { changedFiles, infraChecks, outputs, planChanges, summary } from "../affected.mjs";
import { inert } from "../quarantine.mjs";

const repository = fileURLToPath(new URL("../../", import.meta.url));

export const PLAN_JOB = "aid-gate-plan";
// The applications with an image check: each has a job of its own in gate, as each has its app workflow's image job.
export const imageApps = (root = repository) => readdirSync(posix.join(root, "apps"), { withFileTypes: true })
  .filter((entry) => entry.isDirectory() && existsSync(posix.join(root, "apps", entry.name, "ci/check-image.sh")))
  .map(({ name }) => name).sort();

// Every job of gate but the plan's, each with the plan's output that names it: ci.yml's jobs of the queue's run.
export function gateJobs(root = repository) {
  return [
    // ci.yml's `bazel`: the build and test of everything, then the architecture's actual-edge check.
    { name: "aid-bazel-everything", when: "bazel" },
    { name: "aid-bazel-graph", when: "bazel" },
    { name: "aid-gate-ingress", when: "ingress" },
    // `images`: one job per application, as its matrix has one per application.
    ...imageApps(root).map((app) => ({ name: `aid-gate-image-${app}`, when: "images", app })),
    // `aid-images`, with the release folder they are built from (`release-folder`).
    { name: "aid-gate-aid-images", when: "aid_images" },
    { name: "aid-gate-farm-worker", when: "farm_worker" },
    // `zuul-node-image`, when Zuul's infrastructure check is planned.
    { name: "aid-gate-zuul-node-image", when: "infra_checks", check: "zuul" },
    ...infraChecks.map((check) => ({ name: `aid-gate-infra-${check}`, when: "infra_checks", check })),
    // `staging`, with its own release folder.
    { name: "aid-gate-staging", when: "staging" },
    // The quarantine runs: they block nothing (`voting: false`), as on GitHub Actions.
    { name: "aid-gate-quarantine-swift", when: "quarantine_swift" },
    { name: "aid-gate-quarantine-node", when: "quarantine_node" },
  ];
}

// The jobs a plan's outputs (ci/affected.mjs `outputs`) name. A plan that names an image no gate job builds stops here:
// the gate never passes without a step the plan asked for.
export function plannedJobs(values, root = repository) {
  const images = JSON.parse(values.images), checks = JSON.parse(values.infra_checks);
  const apps = new Set(imageApps(root));
  for (const app of images) if (!apps.has(app)) throw new Error(`The plan names the image of ${app}, which has no apps/${app}/ci/check-image.sh and no gate job.`);
  for (const check of checks) if (!infraChecks.includes(check)) throw new Error(`The plan names the infrastructure check ${check}, which has no gate job.`);
  return [PLAN_JOB, ...gateJobs(root).filter((job) => {
    if (job.when === "images") return images.includes(job.app);
    if (job.when === "infra_checks") return checks.includes(job.check);
    return values[job.when] === "true";
  }).map(({ name }) => name)];
}

// The plan of the checkout at `root` against `base`, as ci.yml's plan job makes it for the queue's run.
export function gatePlan(base, root = repository) {
  let changed = null, why;
  try {
    changed = changedFiles(base, "HEAD", root);
    why = `${changed.length} file(s) differ from ${base}, the state ahead of this change.`;
  } catch (error) {
    why = `The changed files could not be listed against ${base} (${String(error.message).split("\n")[0]}), so everything is tested.`;
  }
  const result = planChanges(changed, { root, base });
  const values = outputs(result);
  return { why, values, jobs: plannedJobs(values, root), summary: summary(result, why) };
}

function main([command, ...args]) {
  if (command === "jobs" && args.length === 0) {
    for (const { name } of gateJobs()) console.log(name);
    return 0;
  }
  if (command === "plan" && args.length === 2) {
    const [base, file] = args;
    const plan = gatePlan(base, process.cwd());
    // File names and the quarantine list's tests are the change's text: kept inert in the log (ci/affected.mjs).
    for (const line of inert(plan.summary.trimEnd().split("\n"))) console.log(line);
    console.log(`\nGate jobs: ${plan.jobs.join(", ")}`);
    mkdirSync(dirname(file), { recursive: true });
    writeFileSync(file, `${JSON.stringify({ child_jobs: plan.jobs.filter((name) => name !== PLAN_JOB), plan: plan.values }, null, 2)}\n`);
    return 0;
  }
  console.error("usage: node ci/zuul/gate.mjs plan <base> <plan.json> | jobs");
  return 2;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  try { process.exitCode = main(process.argv.slice(2)); } catch (error) { console.error(error.message); process.exitCode = 1; }
}
