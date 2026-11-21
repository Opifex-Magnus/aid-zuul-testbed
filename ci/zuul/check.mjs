// check.mjs: the rules of Zuul's layout (README.md, "Checks"), with Node and no packages:
//   (cd ci/zuul && node check.mjs)
// It reads the YAML as text, in the forms these files use (one job's `run:` a path, a playbook's roles a list of
// names), and reports anything in another form rather than guess. ../zuul.test.mjs runs it in CI's Node tests.
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { basename, dirname, join, posix } from "node:path";
import { fileURLToPath } from "node:url";
import { gateJobs, PLAN_JOB } from "./gate.mjs";

// AID's project: where Zuul reads its configuration (extra-config-paths) and where its playbooks are. The trusted half
// is kristofk/aid-zuul-config (README.md, "Trusted and untrusted").
export const UNTRUSTED = "ci/zuul/zuul.d/";
const PLAYBOOKS = "ci/zuul/playbooks/";
// What Zuul reads for every project before extra-config-paths: AID's jobs would have a second place.
const ROOT_CONFIG = ["zuul.yaml", "zuul.d", ".zuul.yaml", ".zuul.d"];
// The library's two jobs, which every job of AID's is or inherits.
const LIBRARY = ["aid-bazel", "aid-script"];
// What a role may do: call a program (Bazel or a script), and check its variables first. The gate's plan role also reads
// the plan the script wrote and hands its jobs to Zuul (gate.md, "The gate's jobs"), and decides nothing itself.
const TASK_KEYS = new Set(["name", "when", "environment", "failed_when", "ansible.builtin.command", "ansible.builtin.assert"]);
const ROLE_TASK_KEYS = { "gate-plan": new Set(["name", "ansible.builtin.slurp", "register", "zuul_return"]) };
// The gate's jobs on the image node, the staging node, and those that block nothing (gate.md, "The gate's jobs").
const GATE_NODESETS = { "aid-gate-image": /^(aid-gate-(image-|aid-images$|farm-worker$|zuul-node-image$))/, "aid-staging": /^aid-gate-staging$/ };
const NON_VOTING = /^aid-gate-quarantine-/;

const yamlFiles = (folder) => existsSync(folder)
  ? readdirSync(folder).filter((name) => /\.ya?ml$/.test(name)).sort().map((name) => join(folder, name))
  : [];

// The top-level items of a Zuul configuration file (`- job:`, `- project:`, ...), each with its kind and its lines.
export function items(text) {
  const found = [];
  for (const line of text.split("\n")) {
    const start = line.match(/^- ([a-z-]+):\s*$/);
    if (start) found.push({ kind: start[1], lines: [] });
    else if (/^\S/.test(line) && !line.startsWith("#")) found.push({ kind: null, lines: [line] });
    else if (found.length) found.at(-1).lines.push(line);
  }
  return found;
}
// A job's own scalar attribute, at the item's first level (four spaces in).
const attribute = (item, key) => {
  for (const line of item.lines) {
    const match = line.match(new RegExp(`^    ${key}:(.*)$`));
    if (match) return match[1].trim().replace(/^(["'])(.*)\1$/, "$2");
  }
  return undefined;
};
// A job's variable, under its `vars:` (six spaces in).
const variable = (item, key) => attribute(item, `  ${key}`);

// The roles a playbook names, in the form `roles:` then `- name` (or `- role: name`) below it.
export function playbookRoles(text) {
  const roles = [];
  let indent = null;
  for (const line of text.split("\n")) {
    if (/^\s*(#|$)/.test(line)) continue;
    const start = line.match(/^(\s*)roles:\s*$/);
    if (start) { indent = start[1].length; continue; }
    if (indent === null) continue;
    const entry = line.match(/^(\s*)- (?:role: )?([A-Za-z0-9_.-]+)\s*$/);
    if (entry && entry[1].length >= indent) roles.push(entry[2]);
    else if (line.match(/^(\s*)/)[1].length <= indent) indent = null;
    else roles.push(null);
  }
  return roles;
}

// The keys of each task of a role's task file (each task a `- ` item at the file's first level).
export function taskKeys(text) {
  const tasks = [];
  for (const line of text.split("\n")) {
    if (/^\s*(#|$)/.test(line)) continue;
    const first = line.match(/^- ([A-Za-z0-9_.-]+):/);
    if (first) { tasks.push([first[1]]); continue; }
    const key = line.match(/^  ([A-Za-z0-9_.-]+):/);
    if (key && tasks.length) tasks.at(-1).push(key[1]);
  }
  return tasks;
}

// What a project stanza says: its own scalar attributes (`merge-mode: rebase`), and the jobs each pipeline runs, in the
// form `<pipeline>:` then `jobs:` then `- <job>` below it. A job given with its own attributes is reported as null.
export function projectStanza(item) {
  const attributes = {}, pipelines = {};
  let pipeline = null, inJobs = false;
  for (const line of item.lines) {
    if (/^\s*(#|$)/.test(line)) continue;
    const top = line.match(/^    ([A-Za-z0-9_-]+):\s*(.*)$/);
    if (top) {
      inJobs = false;
      if (top[2] === "") { pipeline = top[1]; pipelines[pipeline] = []; } else { pipeline = null; attributes[top[1]] = top[2].trim(); }
      continue;
    }
    if (pipeline && /^      jobs:\s*$/.test(line)) { inJobs = true; continue; }
    const job = line.match(/^        - ([A-Za-z0-9_-]+)\s*$/);
    if (pipeline && inJobs && job) pipelines[pipeline].push(job[1]);
    else if (pipeline && inJobs && /^        /.test(line)) pipelines[pipeline].push(null);
    else inJobs = false;
  }
  return { attributes, pipelines };
}

// The extra-config-paths a tenant file gives each source's projects: { source: { "config"|"untrusted": [path] } }.
export function tenantPaths(text) {
  const paths = {};
  let source = null, kind = null, inPaths = false, pathsIndent = 0;
  for (const line of text.split("\n")) {
    if (/^\s*(#|$)/.test(line)) continue;
    const indent = line.match(/^(\s*)/)[1].length;
    if (inPaths && indent > pathsIndent && /^\s*- /.test(line)) {
      paths[source][kind].push(line.trim().slice(2).trim());
      continue;
    }
    inPaths = false;
    const sourceLine = line.match(/^      ([A-Za-z0-9_.-]+):\s*$/);
    if (sourceLine) { source = sourceLine[1]; paths[source] ??= { config: [], untrusted: [] }; continue; }
    const kindLine = line.match(/^        (config|untrusted)-projects:\s*$/);
    if (kindLine) { kind = kindLine[1]; continue; }
    const list = line.match(/^(\s*)extra-config-paths:\s*(\[.*\])?\s*$/);
    if (list && source && kind) {
      if (list[2]) paths[source][kind].push(...list[2].slice(1, -1).split(",").map((path) => path.trim()).filter(Boolean));
      else { inPaths = true; pathsIndent = list[1].length; }
    }
  }
  return paths;
}

export function check(root) {
  const problems = [];
  const at = (path) => join(root, path);
  const read = (path) => readFileSync(at(path), "utf8");

  for (const name of ROOT_CONFIG) {
    if (existsSync(at(name))) problems.push(`${name} at the repository's root: AID's jobs are in ${UNTRUSTED}, and Zuul would read both`);
  }

  // The test bed's tenant reads AID's project from its folder, and the config project's copy only for testbed/: its
  // build node and its jobs with a secret.
  const testTenant = items(read("ci/zuul/testbed/main.yaml")).find(({ kind }) => kind === "tenant");
  if (!testTenant || attribute(testTenant, "use-nodepool") !== "false") {
    problems.push("the test bed's tenant must disable Nodepool fallback with use-nodepool: false");
  }
  const tenant = tenantPaths(read("ci/zuul/testbed/main.yaml"));
  const configPaths = Object.values(tenant).flatMap((source) => source.config);
  const untrustedPaths = Object.values(tenant).flatMap((source) => source.untrusted);
  if (!untrustedPaths.includes(UNTRUSTED)) problems.push(`the test bed's tenant does not read ${UNTRUSTED} into AID's project`);
  for (const path of configPaths) {
    if (path !== "testbed/") problems.push(`the test bed's config project reads ${path}: its copy adds only testbed/`);
  }

  // AID's jobs and their playbooks, and the project stanzas that say which pipeline runs them.
  const jobs = {};
  const nodesets = {};
  const projects = [];
  for (const file of yamlFiles(at(UNTRUSTED))) {
    const where = posix.join(UNTRUSTED, basename(file));
    for (const item of items(readFileSync(file, "utf8"))) {
      if (item.kind === null) { problems.push(`${where}: a top-level line outside a "- kind:" item: ${item.lines[0]}`); continue; }
      if (item.kind === "pipeline") problems.push(`${where}: a pipeline in AID's untrusted project; pipelines are kristofk/aid-zuul-config's`);
      if (item.kind === "project") projects.push({ where, ...projectStanza(item) });
      if (item.kind === "nodeset") nodesets[attribute(item, "name")] = item.lines;
      if (item.kind !== "job") continue;
      const name = attribute(item, "name");
      if (!name) { problems.push(`${where}: a job with no name`); continue; }
      const parent = attribute(item, "parent");
      if (parent === "null") problems.push(`${where}: ${name} is a base job (parent: null); the base job is kristofk/aid-zuul-config's`);
      jobs[name] = { where, parent, abstract: attribute(item, "abstract") === "true", script: variable(item, "script"),
        run: attribute(item, "run"), dependencies: attribute(item, "dependencies"), nodeset: attribute(item, "nodeset"),
        voting: attribute(item, "voting"), scriptArgs: variable(item, "script_args") };
      for (const key of ["pre-run", "run", "post-run", "cleanup-run"]) {
        const path = attribute(item, key);
        if (path === undefined) continue;
        if (key === "cleanup-run") { problems.push(`${where}: job ${name} uses deprecated cleanup-run; use post-run with cleanup: true`); continue; }
        let paths = [path];
        if (path === "") {
          // Accept the cleanup dictionary form used by our jobs; reject unknown list shapes.
          const start = item.lines.indexOf(`    ${key}:`);
          const block = [];
          for (const line of item.lines.slice(start + 1)) {
            if (/^    \S/.test(line)) break;
            if (!/^\s*(#|$)/.test(line)) block.push(line);
          }
          if (key !== "post-run" || !block.length || block.length % 2 ||
              block.some((line, i) => i % 2 ? line !== "        cleanup: true" : !/^      - name: \S+$/.test(line))) {
            problems.push(`${where}: job ${name}'s ${key} is a list; give one playbook or cleanup post-run dictionaries`);
            continue;
          }
          paths = block.filter((_, i) => i % 2 === 0).map(line => line.slice("      - name: ".length));
        }
        for (const path of paths) {
          if (!path.startsWith(PLAYBOOKS)) problems.push(`${where}: job ${name}'s ${key} ${path} is not under ${PLAYBOOKS}`);
          else if (!existsSync(at(path))) problems.push(`${where}: job ${name}'s ${key} ${path} does not exist`);
        }
      }
    }
  }
  for (const [name, job] of Object.entries(jobs)) {
    if (LIBRARY.includes(name)) {
      if (job.parent !== undefined) problems.push(`${job.where}: ${name} inherits ${job.parent}, not the base job`);
      if (job.nodeset !== undefined) problems.push(`${job.where}: shared library ${name} must leave check routing to trusted base`);
      if (!job.abstract) problems.push(`${job.where}: ${name} is not abstract`);
      continue;
    }
    // Every other job of AID's inherits one of the library's, through AID's own jobs.
    const seen = new Set([name]);
    let parent = job.parent;
    while (parent && !LIBRARY.includes(parent) && jobs[parent] && !seen.has(parent)) {
      seen.add(parent);
      parent = jobs[parent].parent;
    }
    if (!LIBRARY.includes(parent)) problems.push(`${job.where}: ${name} inherits neither ${LIBRARY.join(" nor ")}`);
  }

  // Each script a job runs is in the repository, and can be run as aid-script runs it (`./<script>`).
  for (const [name, job] of Object.entries(jobs)) {
    if (job.script === undefined) continue;
    if (!/^(ci|scripts)\/[^ ]+$/.test(job.script) || job.script.split("/").includes("..")) problems.push(`${job.where}: ${name}'s script ${job.script} is not under ci/ or scripts/`);
    else if (!existsSync(at(job.script))) problems.push(`${job.where}: ${name}'s script ${job.script} does not exist`);
    else if ((statSync(at(job.script)).mode & 0o111) === 0) problems.push(`${job.where}: ${name}'s script ${job.script} is not executable`);
  }

  // The project (gate.md): merged by rebase, check and check-external the same cheap jobs, gate the full ones, and
  // every job it names one of AID's that runs (or Zuul's noop).
  const project = projects.find((stanza) => Object.keys(stanza.pipelines).length);
  if (projects.length !== 1 || !project) problems.push(`${UNTRUSTED}: ${projects.length} project stanzas; AID's project has one, which names its pipelines' jobs`);
  else {
    if (project.attributes["merge-mode"] !== "rebase") problems.push(`${project.where}: merge-mode is ${project.attributes["merge-mode"]}, not rebase: main stays linear (gate.md, "Merging")`);
    for (const pipeline of ["check", "check-external", "gate"]) {
      if (!project.pipelines[pipeline]?.length) problems.push(`${project.where}: no jobs in ${pipeline}`);
    }
    if (JSON.stringify(project.pipelines.check) !== JSON.stringify(project.pipelines["check-external"])) {
      problems.push(`${project.where}: check-external runs ${project.pipelines["check-external"]}, check ${project.pipelines.check}: the same cheap jobs`);
    }
    for (const [pipeline, names] of Object.entries(project.pipelines)) {
      for (const name of names) {
        if (name === null) problems.push(`${project.where}: a job in ${pipeline} in a form check.mjs does not read`);
        else if (name === "noop") continue;
        else if (!jobs[name]) problems.push(`${project.where}: ${pipeline} runs ${name}, which is not one of AID's jobs`);
        else if (jobs[name].abstract) problems.push(`${project.where}: ${pipeline} runs ${name}, which is abstract`);
      }
    }
  }

  // The gate (gate.md, "The gate's jobs"): the plan first, then every job the plan can name and no other, each behind
  // the plan, so Zuul runs only those it names; each on the node its step needs, the quarantine runs alone not voting.
  if (project?.pipelines.gate?.length) {
    // Resolve inheritance for plan and all non-image jobs; an omitted child
    // nodeset must never silently fall back to trusted base's check pool.
    const effectiveNodeset = (name) => {
      const seen = new Set();
      while (jobs[name] && !seen.has(name)) {
        seen.add(name);
        if (jobs[name].nodeset !== undefined) return jobs[name].nodeset;
        name = jobs[name].parent;
      }
      return undefined;
    };
    const computeDefinition = nodesets["aid-gate-compute"]?.filter((line) => line.trim() && !line.trim().startsWith("#"));
    if (JSON.stringify(computeDefinition) !== JSON.stringify([
      "    name: aid-gate-compute", "    nodes:", "      - name: node", "        label: aid-gate-compute-pod-node",
    ])) problems.push("aid-gate-compute must declare exactly node on aid-gate-compute-pod-node");
    if (effectiveNodeset(PLAN_JOB) !== "aid-gate-compute") problems.push(`${PLAN_JOB} must run on aid-gate-compute`);
    const expected = [PLAN_JOB, ...gateJobs(root).map(({ name }) => name)];
    if (JSON.stringify(project.pipelines.gate) !== JSON.stringify(expected)) {
      problems.push(`${project.where}: gate runs ${project.pipelines.gate.join(", ")}; the plan's jobs are ${expected.join(", ")} (node ci/zuul/gate.mjs jobs)`);
    }
    const plan = jobs[PLAN_JOB];
    if (plan && plan.run !== `${PLAYBOOKS}gate-plan.yaml`) problems.push(`${plan.where}: ${PLAN_JOB} does not run ${PLAYBOOKS}gate-plan.yaml, which hands its plan to Zuul`);
    if (plan?.dependencies !== undefined) problems.push(`${plan.where}: ${PLAN_JOB} depends on ${plan.dependencies}; it runs first`);
    for (const { name, app, check } of gateJobs(root)) {
      const job = jobs[name];
      if (!job) continue;
      if (job.dependencies !== `[${PLAN_JOB}]`) problems.push(`${job.where}: ${name} does not depend on ${PLAN_JOB} alone (dependencies: [${PLAN_JOB}]), so the plan would not decide whether it runs`);
      const nodeset = Object.entries(GATE_NODESETS).find(([, pattern]) => pattern.test(name))?.[0] ?? "aid-gate-compute";
      const actualNodeset = effectiveNodeset(name);
      if (actualNodeset !== nodeset) problems.push(`${job.where}: ${name} runs on ${actualNodeset ?? "the PR node"}, not ${nodeset}`);
      if ((job.voting === "false") !== NON_VOTING.test(name)) problems.push(`${job.where}: ${name} ${job.voting === "false" ? "does not vote; only the quarantine runs block nothing" : "votes; the quarantine runs block nothing"}`);
      // A job for one application or one check names it last, as the step's <name>.
      const named = app ?? check;
      if (named && name.endsWith(`-${named}`) && !(job.scriptArgs ?? "").replace(/\s/g, "").endsWith(`,${named}]`)) problems.push(`${job.where}: ${name}'s script_args do not end with ${named}`);
    }
  }

  // Every role a playbook names is beside it, and does only what a role may.
  for (const file of yamlFiles(at(PLAYBOOKS))) {
    const where = posix.join(PLAYBOOKS, basename(file));
    for (const role of playbookRoles(readFileSync(file, "utf8"))) {
      if (role === null) problems.push(`${where}: a roles entry in a form check.mjs does not read`);
      else if (!existsSync(at(`${PLAYBOOKS}roles/${role}/tasks/main.yaml`))) problems.push(`${where}: role ${role} is not in ${PLAYBOOKS}roles/`);
    }
  }
  const roles = at(`${PLAYBOOKS}roles`);
  for (const role of existsSync(roles) ? readdirSync(roles).sort() : []) {
    const tasks = `${PLAYBOOKS}roles/${role}/tasks/main.yaml`;
    if (!existsSync(at(tasks))) { problems.push(`role ${role} has no tasks/main.yaml`); continue; }
    const others = readdirSync(at(`${PLAYBOOKS}roles/${role}`)).filter((name) => !["tasks", "defaults"].includes(name));
    if (others.length) problems.push(`role ${role} has ${others.join(", ")}: a role here has only tasks/ and defaults/`);
    const allowed = ROLE_TASK_KEYS[role] ?? TASK_KEYS;
    for (const keys of taskKeys(read(tasks))) {
      const extra = keys.filter((key) => !allowed.has(key));
      if (extra.length) problems.push(`${tasks}: a task with ${extra.join(", ")}: ${ROLE_TASK_KEYS[role] ? `the role ${role} only reads the plan and hands it to Zuul` : "a role runs only ansible.builtin.command and ansible.builtin.assert"}`);
    }
  }

  // The test bed's node runs the Bazelisk the boxes do.
  const pinned = read("mise.toml").match(/^bazelisk = "([^"]+)"$/m)?.[1];
  const node = read("ci/zuul/testbed/node/Dockerfile").match(/^ARG BAZELISK_VERSION=(\S+)$/m)?.[1];
  if (!pinned || pinned !== node) problems.push(`the test bed's node has Bazelisk ${node}, mise.toml pins ${pinned}`);
  return problems;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const problems = check(join(dirname(fileURLToPath(import.meta.url)), "../.."));
  for (const problem of problems) console.log(`PROBLEM: ${problem}`);
  if (problems.length) process.exit(1);
  console.log("ci/zuul: the layout keeps its rules");
}
