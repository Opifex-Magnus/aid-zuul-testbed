# The test bed's build node (../README.md, "The test bed"): a stand-in for #1345's node image, with what the base job
# and AID's jobs use on a node: an SSH server for the executor, Python for Ansible, git and rsync for the checkout, and
# Bazelisk as `bazel`.
FROM ubuntu:24.04
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        ca-certificates curl git openssh-server python3 rsync \
    && rm -rf /var/lib/apt/lists/*
# Bazelisk, the version mise.toml pins, checked against its release's SHA-256.
ARG BAZELISK_VERSION=1.30.0
ARG BAZELISK_SHA256=a248ae2b2b62b5baebb748f6fba7203bff1a27e5d01b55399a8406744cb539e8
RUN curl -fsSL -o /usr/local/bin/bazel \
        "https://github.com/bazelbuild/bazelisk/releases/download/v${BAZELISK_VERSION}/bazelisk-linux-amd64" \
    && echo "${BAZELISK_SHA256}  /usr/local/bin/bazel" | sha256sum -c - \
    && chmod 755 /usr/local/bin/bazel
# The user the executor logs in as. Its authorized_keys is mounted from the run's work folder, owned by whoever ran it,
# so sshd's ownership check is off.
RUN useradd --create-home --shell /bin/bash zuul \
    && install -d -o zuul -g zuul -m 700 /home/zuul/.ssh \
    && mkdir -p /run/sshd \
    && sed -i 's/^#\?StrictModes .*/StrictModes no/' /etc/ssh/sshd_config
EXPOSE 22
CMD ["/usr/sbin/sshd", "-D", "-e"]
