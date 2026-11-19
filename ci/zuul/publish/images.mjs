// Which of AID's infrastructure images a merge into main publishes (#1650): the job aid-publish-images in post
// (ci/zuul/publish/images.sh, ci/zuul/publish.md). Each image publishes when the push changed one of its inputs, which
// ci/inputs.json lists (zuulNode, farmWorker) and gate checks it for, and only while ci/publisher names this CI.
//
//   node ci/zuul/publish/images.mjs <image>   publish=true|false, and the image's repository=
//
// The push's files are read as ci/image-policy.mjs reads them for the applications, which this does not import from:
// a change to ci/image-*.mjs is every application's image input (ci/inputs.json), so changing that file republishes
// them all.
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { farmWorkerPaths, zuulNodePaths } from "../../affected.mjs";
import { REPOSITORY as WORKER } from "../../build-farm-worker.mjs";
import { publishesHere } from "../../publisher.mjs";
import { REPOSITORY as NODE } from "../../zuul-node-image.mjs";

export const IMAGES = {
  // The PR node's and the post node's images, both from deploy/zuul/node/image/ (ci/zuul-node-image.mjs).
  "zuul-node": { repository: NODE, inputs: zuulNodePaths },
  "build-farm-worker": { repository: WORKER, inputs: farmWorkerPaths },
};

/** The files the push changed: every file of the new commit for a branch's first push. */
export function pushedPaths(env, git) {
  const event = JSON.parse(readFileSync(env.GITHUB_EVENT_PATH, "utf8"));
  if (!/^[0-9a-f]{40}$/.test(env.GITHUB_SHA || "") || !/^[0-9a-f]{40}$/.test(event.before || "")) throw new Error("push has no valid commit range");
  if (/^0+$/.test(event.before)) return git(["ls-tree", "-r", "--name-only", "-z", env.GITHUB_SHA]).split("\0").filter(Boolean);
  git(["fetch", "--no-tags", "--depth=1", "origin", event.before]);
  return git(["diff", "--no-renames", "--name-only", "-z", event.before, env.GITHUB_SHA]).split("\0").filter(Boolean);
}

/** Whether this push publishes the image: a push to main on ThunderBox that changed its inputs, by the named CI. */
export function shouldPublish(name, env = process.env, git = (args) => execFileSync("git", args, { encoding: "utf8" }), root = process.cwd()) {
  const image = IMAGES[name];
  if (!image) throw new Error(`'${name}' is none of ${Object.keys(IMAGES).join(", ")}`);
  if (!publishesHere(env, root) || env.GITHUB_EVENT_NAME !== "push" || env.GITHUB_REF !== "refs/heads/main" || env.CI_RUNNER !== "self-hosted") return false;
  const inputs = image.inputs();
  return pushedPaths(env, git).some((path) => inputs.some((pattern) => pattern.test(path)));
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  try {
    const [name] = process.argv.slice(2);
    const publish = shouldPublish(name);
    console.log(`publish=${publish}\nrepository=${IMAGES[name].repository}`);
  } catch (error) {
    console.error(`images: ${error.message}`);
    process.exitCode = 1;
  }
}
