"""The test bed's change to Zuul for its token (run.sh, "== patch"), on copies of two files of Zuul's GitHub driver:
  python3 patch.py <folder of zuul/driver/github/graphql> <output folder>

For every pull request Zuul asks GitHub's GraphQL API for the repository's branch protection rules (its "can merge"
query). That needs the repository's administration permission, which a workflow's token cannot have (ThunderBox's
GitHub App gets it, #1344). GitHub then answers with no repository at all, and Zuul 14.3.0 fails the event. So the test
bed's copy of the query leaves the rules out, and its copy of the driver reads no rules as an unprotected branch. Each
change must apply exactly as written, or the patch stops: a new Zuul needs it looked at again.
"""

import sys
from pathlib import Path


def drop_rules(query):
    """canmerge.graphql without its branchProtectionRules block, and without the variable only that block used."""
    lines = query.split("\n")
    starts = [i for i, line in enumerate(lines) if line == "    branchProtectionRules(first: 100) {"]
    if len(starts) != 1:
        raise SystemExit(f"canmerge.graphql has {len(starts)} branchProtectionRules blocks, not 1")
    end, depth = starts[0], 0
    while True:
        depth += lines[end].count("{") - lines[end].count("}")
        if depth == 0:
            break
        end += 1
    kept = lines[:starts[0]] + lines[end + 1:]
    if kept.count("  $ref_name: String!") != 1 or any("$ref_name" in line for line in kept if line != "  $ref_name: String!"):
        raise SystemExit("canmerge.graphql uses $ref_name outside the branch protection rules")
    return "\n".join(line for line in kept if line != "  $ref_name: String!")


def tolerate_no_rules(source):
    """The driver's two reads of the rules' page info, with none read as the last page."""
    old = "            if not rules_pageinfo['hasNextPage']:"
    new = "            if not rules_pageinfo or not rules_pageinfo['hasNextPage']:"
    if source.split("\n").count(old) != 2:
        raise SystemExit("graphql/__init__.py no longer has the two lines the test bed patches")
    return "\n".join(new if line == old else line for line in source.split("\n"))


def main():
    source, output = Path(sys.argv[1]), Path(sys.argv[2])
    output.mkdir(parents=True, exist_ok=True)
    (output / "canmerge.graphql").write_text(drop_rules((source / "canmerge.graphql").read_text()))
    (output / "__init__.py").write_text(tolerate_no_rules((source / "__init__.py").read_text()))


if __name__ == "__main__":
    main()
