"""Launcher entrypoint for the pinned, tested fixed-namespace adapter."""
from importlib.metadata import version

if version('zuul') != '14.3.0':
    raise RuntimeError('revalidate the bounded Kubernetes adapter before upgrading Zuul')
from bounded import install
install()
from zuul.cmd.launcher import main
main()
