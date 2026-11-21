"""Farm rule tests use the farm's existing Prometheus/mise version source."""
def _impl(ctx):
    pin = ctx.read(ctx.attr.pins)
    version = pin.split('promtool = "')[1].split('"')[0]
    # Fail closed on an upgrade until its release checksum is reviewed.
    sums = {"3.7.3": "fc9e5da126817438cf2820d9a7206c75e3122802ba8d20add3a6219a59ca6913"}
    ctx.download_and_extract(
        url = "https://github.com/prometheus/prometheus/releases/download/v%s/prometheus-%s.linux-amd64.tar.gz" % (version, version),
        sha256 = sums[version],
        stripPrefix = "prometheus-%s.linux-amd64" % version,
    )
    ctx.file("BUILD.bazel", 'exports_files(["promtool"])')

promtool_repository = repository_rule(
    implementation = _impl,
    attrs = {"pins": attr.label(default = "//deploy/build-farm:mise.toml")},
)
