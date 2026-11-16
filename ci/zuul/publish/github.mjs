#!/usr/bin/env node
// What Zuul's publishing jobs do on GitHub (ci/zuul/publish.md), with the App aid-release's token from
// CI_GITHUB_TOKEN_FILE: the calls GitHub Actions' jobs make through actions/github-script, here through
// ci/github-rest.mjs, to the same functions.
//
//   node ci/zuul/publish/github.mjs release <released dir>   the GitHub Release aid-v<version>, from the entry and assets
//   node ci/zuul/publish/github.mjs report <version>          a failed release's issue (CI_RUN_URL: the build)
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { createRelease, releaseAssets, reportFailure } from "../../aid-release.mjs";
import { github } from "../../github-rest.mjs";

export const REPO = { owner: "kristofk", repo: "AIDepartment" };

export function client(env = process.env, read = readFileSync) {
  if (!env.CI_GITHUB_TOKEN_FILE) throw new Error("CI_GITHUB_TOKEN_FILE is required");
  const token = read(env.CI_GITHUB_TOKEN_FILE, "utf8").trim();
  if (!token) throw new Error("the token file is empty");
  // AID_GITHUB_API only for ci/zuul-publish.test.mjs, which answers as GitHub does.
  return github({ token, ...(env.AID_GITHUB_API ? { apiUrl: env.AID_GITHUB_API, uploadUrl: env.AID_GITHUB_API } : {}) });
}

export async function main(args, env = process.env, api = () => client(env)) {
  const [command, value, ...rest] = args;
  if (command === "release" && value && rest.length === 0) {
    const entry = JSON.parse(readFileSync(join(value, "aid-release.json"), "utf8"));
    const release = await createRelease(api(), REPO, entry, releaseAssets(value));
    return `Released ${entry.release} at ${entry.commit}: ${release.html_url}`;
  }
  if (command === "report" && rest.length === 0) {
    const { number, created } = await reportFailure(api(), REPO, { version: value || null, runUrl: env.CI_RUN_URL || "(no build URL)", ci: "zuul" });
    return `${created ? "Opened" : "Commented on"} issue #${number}.`;
  }
  throw new Error("usage: github.mjs release <released dir> | report <version>");
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  try { console.log(await main(process.argv.slice(2))); }
  // The client's errors name the request and its status, never the token or an answer's body.
  catch (error) { console.error(`github: ${error.message}`); process.exitCode = 1; }
}
