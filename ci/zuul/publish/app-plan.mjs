// Recover published app versions whose index write failed, including on a later, unrelated push (#1760).
import { execFileSync } from "node:child_process";
import { appendFileSync, existsSync, readdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { isDeepStrictEqual } from "node:util";
import { Agent as HttpAgent, request as httpRequest } from "node:http";
import { Agent as HttpsAgent, request as httpsRequest } from "node:https";
import { readApp } from "../../app-image.mjs";
import { publicationPolicy } from "../../image-policy.mjs";
import { publishesHere } from "../../publisher.mjs";
import { emptyIndex, FETCH, GIT_MAX_BUFFER, parseIndex } from "../../release-index.mjs";

export function currentIndex(root = process.cwd()) {
  const git = (args) => execFileSync("git", args, { cwd: root, encoding: "utf8", maxBuffer: GIT_MAX_BUFFER, stdio: ["ignore", "pipe", "pipe"] });
  try { git([...FETCH, "origin", "refs/heads/release-index"]); }
  catch (error) {
    if (/couldn't find remote ref/i.test(String(error.stderr))) return emptyIndex();
    throw new Error("cannot fetch the release index");
  }
  return parseIndex(git(["show", "FETCH_HEAD:index.json"]));
}

// This endpoint is read-only and needs no registry password. A failed lookup must never become permission to
// overwrite a tag. Only 404 means absent; the OCI and Docker v2 Accept types preserve the registry's digest.
// Node's global agents honour NODE_USE_ENV_PROXY too. Use an explicitly direct agent for this internal endpoint;
// keep git's and the rest of the job's environment intact, since GitHub still needs the public proxy.
function registryHead(url, options) {
  return new Promise((resolve, reject) => {
    const secure = url.protocol === "https:";
    const agent = secure ? new HttpsAgent({ proxyEnv: {} }) : new HttpAgent({ proxyEnv: {} });
    const request = (secure ? httpsRequest : httpRequest)(url, { ...options, agent }, (response) => {
      response.resume();
      resolve({ status: response.statusCode, headers: { get: (name) => response.headers[name.toLowerCase()] } });
    });
    request.on("close", () => agent.destroy());
    request.on("error", reject);
    request.end();
  });
}

export async function registryDigest(repository, version, { env = process.env, fetch = registryHead } = {}) {
  const base = new URL(env.CI_RELEASE_REGISTRY);
  if (!["http:", "https:"].includes(base.protocol) || base.username || base.password || base.search || base.hash || base.pathname !== "/") {
    throw new Error("CI_RELEASE_REGISTRY must be an anonymous HTTP(S) origin");
  }
  const response = await fetch(new URL(`/v2/${repository}/manifests/${encodeURIComponent(version)}`, base), {
    method: "HEAD", redirect: "error", signal: AbortSignal.timeout(20_000),
    headers: { Accept: "application/vnd.oci.image.manifest.v1+json, application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.v2+json, application/vnd.docker.distribution.manifest.list.v2+json" },
  });
  if (response.status === 404) return null;
  if (response.status !== 200) throw new Error(`registry lookup for ${repository}:${version} answered HTTP ${response.status}`);
  const digest = response.headers.get("docker-content-digest");
  if (!/^sha256:[0-9a-f]{64}$/.test(digest ?? "")) throw new Error(`registry lookup for ${repository}:${version} has no valid digest`);
  return digest;
}

export async function appPlan({ root = process.cwd(), env = process.env, index = () => currentIndex(root), lookup = registryDigest,
  selected = (app) => publicationPolicy(app, env, undefined, root), notice = (message) => {
    console.log(message);
    if (env.GITHUB_STEP_SUMMARY) appendFileSync(env.GITHUB_STEP_SUMMARY, `${message}\n`);
  } } = {}) {
  if (!publishesHere(env, root)) return [];
  if (env.GITHUB_EVENT_NAME !== "push" || env.GITHUB_REF !== "refs/heads/main" || env.CI_RUNNER !== "self-hosted") {
    throw new Error("app publication requires a self-hosted push to main");
  }
  const released = index();
  const plan = [];
  for (const app of readdirSync(join(root, "apps")).sort()) {
    if (!existsSync(join(root, "apps", app, "ci/check-image.sh"))) continue;
    const source = await readApp(app, { root });
    const changed = selected(app);
    const entry = released.apps[app]?.versions[source.declaration.version];
    if (entry && !changed) continue;
    const digest = await lookup(source.repository, source.declaration.version, { env });
    if (digest) {
      const declaration = source.releaseDeclaration(source.declaration, digest);
      if (entry) {
        if (!isDeepStrictEqual(entry, declaration)) throw new Error(`${app}: published version differs from the source or registry; bump its version`);
        notice(`${app} ${source.declaration.version} is already published; its payload change needs a version bump.`);
        continue;
      }
      plan.push({ app, digest });
    } else if (entry) {
      throw new Error(`${app}: indexed version is missing from the registry`);
    } else if (changed) plan.push({ app, digest: null });
  }
  return plan;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  try {
    const plan = await appPlan();
    // Write only a complete plan: a failed lookup cannot leave a partial list for the next stage.
    writeFileSync(process.argv[2], plan.map(({ app, digest }) => `${app} ${digest ?? "build"}\n`).join(""));
  } catch (error) { console.error(`app-plan: ${error.message}`); process.exitCode = 1; }
}
