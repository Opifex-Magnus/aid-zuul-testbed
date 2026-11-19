"""aid-zuul-config's pipelines on GitLab, for the GitLab test bed (../../forge.md, "The check on GitLab").

  python3 translate.py <original pipelines.yaml> <gitlab pipelines.yaml> <out>

Swaps, in each section of each pipeline of the original, its github entry for the gitlab file's entry of the same
pipeline and section (pipelines.yaml says why each differs). Keeps everything else as it is. Fails if a pipeline of the
original uses github and the gitlab file has no such pipeline, if the gitlab file has a pipeline or a section that has no
github entry in the original to stand in for, or if github is left anywhere. Runs in Zuul's scheduler image, which has
PyYAML: run.sh starts it there.
"""

import sys

import yaml


def pipelines(path):
    with open(path) as handle:
        documents = yaml.safe_load(handle)
    return [item["pipeline"] for item in documents if "pipeline" in item]


def main(original_path, gitlab_path, out_path):
    original = pipelines(original_path)
    gitlab = {pipeline["name"]: pipeline for pipeline in pipelines(gitlab_path)}
    problems = []
    names = {pipeline["name"] for pipeline in original}
    for name in sorted(set(gitlab) - names):
        problems.append(f"{gitlab_path}: pipeline {name} is not in {original_path}")
    out = []
    for pipeline in original:
        name = pipeline["name"]
        forge = gitlab.get(name, {})
        uses_github = [key for key, value in pipeline.items() if isinstance(value, dict) and "github" in value]
        if uses_github and name not in gitlab:
            problems.append(f"{original_path}: pipeline {name} uses github in {', '.join(uses_github)}, and {gitlab_path} has no {name}")
        for key, value in forge.items():
            if key == "name":
                continue
            if key not in uses_github:
                problems.append(f"{gitlab_path}: {name}.{key} stands in for no github entry of the original")
        translated = {}
        for key, value in pipeline.items():
            if key in uses_github:
                value = {driver: entry for driver, entry in value.items() if driver != "github"}
                if key in forge:
                    value["gitlab"] = forge[key]["gitlab"]
                if not value:
                    print(f"{name}.{key}: reported nothing but GitHub's, so nothing on GitLab")
                    continue
            translated[key] = value
        out.append({"pipeline": translated})
    def keys(value):
        if isinstance(value, dict):
            return set(value) | set().union(*map(keys, value.values()))
        if isinstance(value, list):
            return set().union(*map(keys, value))
        return set()
    if "github" in keys(out):
        problems.append(f"{out_path}: github is still a connection there")
    text = yaml.safe_dump(out, sort_keys=False, width=120)
    if problems:
        sys.exit("\n".join(problems))
    with open(out_path, "w") as handle:
        handle.write(f"# Made by the GitLab test bed's translate.py from {original_path} and {gitlab_path}.\n")
        handle.write(text)
    print(f"{out_path}: {len(out)} pipelines, {sum(1 for p in original if p['name'] in gitlab)} on GitLab")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    main(*sys.argv[1:])
