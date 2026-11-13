// check.mjs: the rules of Zuul's layout (README.md, "Checks"), with Node and no packages:
//   (cd ci/zuul && node check.mjs)
// It reads the YAML as text, in the forms these files use (one job's `run:` a path, a playbook's roles a list of
// names), and reports anything in another form rather than guess. ../zuul.test.mjs runs it in CI's Node tests.
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { dirname, join, posix } from "node:path";
import { fileURLToPath } from "node:url";

// The two halves (README.md, "One repository, two projects"), as the tenants name them in extra-config-paths.
export const TRUSTED = "ci/zuul/config/zuul.d/";
export const UNTRUSTED = "ci/zuul/zuul.d/";
// Where each half's playbooks are.
const PLAYBOOKS = { [TRUSTED]: "ci/zuul/config/playbooks/", [UNTRUSTED]: "ci/zuul/playbooks/" };
// What Zuul reads for every project, so the config project too.
const ROOT_CONFIG = ["zuul.yaml", "zuul.d", ".zuul.yaml", ".zuul.d"];
// The library's two jobs, which every job of AID's is or inherits.
const LIBRARY = ["aid-bazel", "aid-script"];
// What a role may do: call a program (Bazel or a script), and check its variables first.
const TASK_KEYS = new Set(["name", "when", "ansible.builtin.command", "ansible.builtin.assert"]);

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
    if (existsSync(at(name))) problems.push(`${name} at the repository's root: Zuul would load it for both projects, the config project's as trusted`);
  }
  if (TRUSTED.startsWith(UNTRUSTED) || UNTRUSTED.startsWith(TRUSTED)) problems.push(`${TRUSTED} and ${UNTRUSTED} overlap`);

  // The test bed's tenant reads each half from its own project, and nothing of the other's.
  const tenant = tenantPaths(read("ci/zuul/testbed/main.yaml"));
  const configPaths = Object.values(tenant).flatMap((source) => source.config);
  const untrustedPaths = Object.values(tenant).flatMap((source) => source.untrusted);
  if (!configPaths.includes(TRUSTED)) problems.push(`the test bed's tenant does not read ${TRUSTED} into the config project`);
  if (!untrustedPaths.includes(UNTRUSTED)) problems.push(`the test bed's tenant does not read ${UNTRUSTED} into AID's project`);
  for (const path of untrustedPaths) {
    if (path.startsWith("ci/zuul/config/") || "ci/zuul/config/".startsWith(path)) problems.push(`the test bed's untrusted project reads ${path}, the trusted half's`);
  }
  for (const path of configPaths) {
    if (path.startsWith(UNTRUSTED) || UNTRUSTED.startsWith(path)) problems.push(`the test bed's config project reads ${path}, AID's project's`);
  }

  // Each half's jobs and their playbooks.
  const jobs = {};
  for (const half of [TRUSTED, UNTRUSTED]) {
    for (const file of yamlFiles(at(half))) {
      const where = posix.join(half, file.slice(at(half).length + 1));
      for (const item of items(readFileSync(file, "utf8"))) {
        if (item.kind === null) { problems.push(`${where}: a top-level line outside a "- kind:" item: ${item.lines[0]}`); continue; }
        if (item.kind === "pipeline" && half === UNTRUSTED) problems.push(`${where}: a pipeline in AID's untrusted project`);
        if (item.kind !== "job") continue;
        const name = attribute(item, "name");
        if (!name) { problems.push(`${where}: a job with no name`); continue; }
        jobs[name] = { half, where, parent: attribute(item, "parent"), abstract: attribute(item, "abstract") === "true" };
        for (const key of ["pre-run", "run", "post-run", "cleanup-run"]) {
          const path = attribute(item, key);
          if (path === undefined) continue;
          if (path === "") { problems.push(`${where}: job ${name}'s ${key} is a list; give one playbook`); continue; }
          if (!path.startsWith(PLAYBOOKS[half])) problems.push(`${where}: job ${name}'s ${key} ${path} is not under ${PLAYBOOKS[half]}`);
          else if (!existsSync(at(path))) problems.push(`${where}: job ${name}'s ${key} ${path} does not exist`);
        }
      }
    }
  }
  if (jobs.base?.half !== TRUSTED || jobs.base.parent !== "null") problems.push(`the base job, with parent: null, is not in ${TRUSTED}`);
  for (const [name, job] of Object.entries(jobs)) {
    if (job.half !== UNTRUSTED) continue;
    if (LIBRARY.includes(name)) {
      if (job.parent !== undefined) problems.push(`${job.where}: ${name} inherits ${job.parent}, not the base job`);
      if (!job.abstract) problems.push(`${job.where}: ${name} is not abstract`);
      continue;
    }
    // Every other job of AID's inherits one of the library's, through AID's own jobs.
    const seen = new Set([name]);
    let parent = job.parent;
    while (parent && !LIBRARY.includes(parent) && jobs[parent]?.half === UNTRUSTED && !seen.has(parent)) {
      seen.add(parent);
      parent = jobs[parent].parent;
    }
    if (!LIBRARY.includes(parent)) problems.push(`${job.where}: ${name} inherits neither ${LIBRARY.join(" nor ")}`);
  }

  // Every role a playbook names is beside it, and does only what a role may.
  for (const half of Object.values(PLAYBOOKS)) {
    for (const file of yamlFiles(at(half))) {
      const where = posix.join(half, file.slice(at(half).length + 1));
      for (const role of playbookRoles(readFileSync(file, "utf8"))) {
        if (role === null) problems.push(`${where}: a roles entry in a form check.mjs does not read`);
        else if (half === PLAYBOOKS[UNTRUSTED] && !existsSync(at(`${half}roles/${role}/tasks/main.yaml`))) {
          problems.push(`${where}: role ${role} is not in ${half}roles/`);
        }
      }
    }
  }
  const roles = at(`${PLAYBOOKS[UNTRUSTED]}roles`);
  for (const role of existsSync(roles) ? readdirSync(roles).sort() : []) {
    const tasks = `${PLAYBOOKS[UNTRUSTED]}roles/${role}/tasks/main.yaml`;
    if (!existsSync(at(tasks))) { problems.push(`role ${role} has no tasks/main.yaml`); continue; }
    const others = readdirSync(at(`${PLAYBOOKS[UNTRUSTED]}roles/${role}`)).filter((name) => !["tasks", "defaults"].includes(name));
    if (others.length) problems.push(`role ${role} has ${others.join(", ")}: a role here has only tasks/ and defaults/`);
    for (const keys of taskKeys(read(tasks))) {
      const extra = keys.filter((key) => !TASK_KEYS.has(key));
      if (extra.length) problems.push(`${tasks}: a task with ${extra.join(", ")}: a role runs only ansible.builtin.command and ansible.builtin.assert`);
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
