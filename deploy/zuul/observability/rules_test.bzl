"""Run the pinned Prometheus evaluator on the farm, with fixtures in runfiles."""
def _impl(ctx):
    script = ctx.actions.declare_file(ctx.label.name + ".sh")
    ctx.actions.write(script, """#!/bin/bash
set -eu
cd "$TEST_SRCDIR/$TEST_WORKSPACE"
exec "%s" test rules "%s"
""" % (ctx.file.tool.short_path, ctx.file.fixture.short_path), is_executable = True)
    return [DefaultInfo(executable = script, runfiles = ctx.runfiles(files = [ctx.file.tool, ctx.file.fixture, ctx.file.rules]))]

prometheus_rule_test = rule(
    implementation = _impl,
    test = True,
    attrs = {
        "tool": attr.label(default = "@ci_promtool//:promtool", allow_single_file = True),
        "fixture": attr.label(allow_single_file = True),
        "rules": attr.label(allow_single_file = True),
    },
)
