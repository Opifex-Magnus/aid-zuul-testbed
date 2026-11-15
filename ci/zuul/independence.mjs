// CI's runtime routes must keep working when AID is down (#1506).
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export const runtimeDirectories = [
  "deploy/zuul/server", "deploy/zuul/executor", "deploy/zuul/node", "deploy/zuul/namespace",
  "deploy/build-farm/server", "deploy/build-farm/runners", "deploy/build-farm/namespace",
  "ci/zuul/playbooks", "ci/zuul/zuul.d",
];
// Optional consumers (events, UIs and monitoring) deliberately live outside this set. [web] root is a report
// link, supplied by a Secret, rather than an HTTP dependency. Reject ingress addresses anywhere in these files.
const ingress = /hooks_?url|\/hooks\/zuul-hooks|\baid-(?:ingress|gateway)\b|\baid-apps(?![-\w])|\baid\.[\w.<>{}-]+\.ts\.net/ig;
function files(root, directory) {
  return readdirSync(join(root, directory), { withFileTypes: true }).flatMap((entry) => {
    const path = `${directory}/${entry.name}`;
    return entry.isDirectory() ? files(root, path) : [path];
  });
}

// Step 29's forwarder (#1528) is AID's apps' way to zuul-web and the logs, on aid-apps. It must stay apart from step 14's:
// another Compose project, another address, never on ci_zuul_hooks, and not let in by the webhook policy.
function uiForwarder(runbook, hooks, policies) {
  const problems = [];
  const step = runbook.split("\n29. **")[1]?.split("\n30. **")[0] ?? "";
  const yaml = step.match(/```yaml\n([\s\S]*?)\n\s*```/)?.[1];
  if (!yaml) return ["deploy/thunderbox/zuul.md: step 29 has no UI forwarder YAML"];
  const code = (text) => text.split("\n").filter((line) => !/^\s*#/.test(line)).join("\n");
  const project = (text) => code(text ?? "").match(/^\s*name: (\S+)\s*$/m)?.[1];
  const address = (text) => code(text ?? "").match(/ipv4_address: (\S+)/)?.[1];
  if (/ci_zuul_hooks/.test(code(yaml))) problems.push("deploy/thunderbox/zuul.md: step 29's UI forwarder joins ci_zuul_hooks");
  if (!project(yaml) || project(yaml) === project(hooks)) {
    problems.push("deploy/thunderbox/zuul.md: step 29's UI forwarder is not a Compose project of its own");
  }
  const ui = address(yaml);
  if (!ui || ui === address(hooks)) problems.push("deploy/thunderbox/zuul.md: step 29's UI forwarder has no address of its own");
  const webHooks = policies.split(/^---$/m).find((doc) => /^\s*name: web-hooks\s*$/m.test(doc)) ?? "";
  const admitted = [...code(webHooks).matchAll(/cidr: (\S+)/g)].map((m) => m[1]);
  if (admitted.join(",") !== `${address(hooks)}/32`) {
    problems.push(`deploy/zuul/namespace/networkpolicy.yaml: web-hooks lets in ${admitted.join(", ") || "nothing"}, not only the webhook forwarder`);
  }
  return problems;
}

export function checkIndependence(root) {
  const problems = [];
  const read = (path) => readFileSync(join(root, path), "utf8");
  const inspect = (path, text) => {
    for (const [index, line] of text.split("\n").entries()) {
      // Explanatory comments and Markdown aren't executable routes; job names such as aid-apps-lint are fine.
      if (/^\s*#/.test(line)) continue;
      for (const match of line.matchAll(ingress)) problems.push(`${path}:${index + 1}: CI names AID's ingress (${match[0]})`);
    }
  };
  for (const directory of runtimeDirectories) {
    for (const path of files(root, directory).filter((path) => !path.endsWith(".md"))) inspect(path, read(path));
  }
  const runbook = read("deploy/thunderbox/zuul.md");
  const step = runbook.split("14. **")[1]?.split("14a. **")[0] ?? "";
  const forwarder = step.match(/```yaml\n([\s\S]*?)\n    ```/)?.[1];
  if (!forwarder) problems.push("deploy/thunderbox/zuul.md: step 14 has no forwarder YAML");
  else {
    inspect("deploy/thunderbox/zuul.md (forwarder)", forwarder);
    if (!/^\s+name: ci_zuul_hooks\s*$/m.test(forwarder) || /ZUUL_FORWARD_NETWORK/.test(forwarder)) {
      problems.push("deploy/thunderbox/zuul.md: forwarder must use ci_zuul_hooks without ZUUL_FORWARD_NETWORK");
    }
    if (/TCP4-LISTEN:8080|zuul-logs/.test(forwarder)) problems.push("deploy/thunderbox/zuul.md: CI forwarder exposes optional logs");
  }
  const ui = uiForwarder(runbook, forwarder, read("deploy/zuul/namespace/networkpolicy.yaml"));
  problems.push(...ui);
  const starts = [...step.matchAll(/^    sudo docker compose -f (zuul-(?:hooks|forward)\.yml) up -d$/gm)].map((m) => m[1]);
  if (starts.join(",") !== "zuul-hooks.yml,zuul-forward.yml") problems.push("deploy/thunderbox/zuul.md: start hooks before forwarder");
  const url = "https://zuul-hooks.taila5826.ts.net/api/connection/github/payload";
  const appURL = read("deploy/zuul/github.md").match(/^- \*\*Webhook URL:\*\* `([^`]+)`/m)?.[1];
  const webhookStep = runbook.split("15. **")[1]?.split("16. **")[0] ?? "";
  if (appURL !== url || !webhookStep.includes(`\`${url}\``)) problems.push("Webhook URL must be Zuul's dedicated Funnel payload URL");
  return problems;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const problems = checkIndependence(join(dirname(fileURLToPath(import.meta.url)), "../.."));
  for (const problem of problems) console.error(`PROBLEM: ${problem}`);
  if (problems.length) process.exitCode = 1;
  else console.log("CI independence: runtime routes and webhook forwarder do not name AID's ingress; the UI forwarder is separate");
}
