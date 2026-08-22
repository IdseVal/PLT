# The reproducible toolchain every agent runs inside.
#
# It pins the toolchain so a test that passes here passes on any machine, and it is the shape
# the project deploys in later. It is NOT a sandbox: the container is not a security boundary.
#
# Orca is not installed. It is a desktop application on the host that dispatches agents and
# owns the worktrees; it cannot live inside the environment it dispatches into, and it is not
# published to npm at all.

FROM mcr.microsoft.com/devcontainers/typescript-node:1-22-bookworm

# git and gh: the daily digest is derived from commit and pull-request history, so the
# container reads both. jq keeps shell-side JSON handling out of Python.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        gh \
        jq \
        curl \
        python3 \
        python3-pip \
        python3-venv \
    && rm -rf /var/lib/apt/lists/*

# The `1-22` tag ships Node 22.16, BELOW the 22.20.0 floor the `skills` CLI declares. npm runs
# it anyway with an EBADENGINE warning; a toolchain that works by warning breaks the day npm
# enforces it. `n` pins the interpreter above the floor whatever the base image moves to.
#
# Nothing is chained onto this step. `n` replaces /usr/local/bin/node underneath the running
# shell, so a subsequent npm in the same RUN resolves to the binary just swapped out.
RUN npm install -g n && n 22.20.0

# Debian marks the system interpreter externally managed, so a virtualenv is the supported way
# to install into it. Put it OUTSIDE the workspace: a bind-mounted workspace shared with a
# Windows or macOS host would otherwise have the container's Linux venv overwrite the host's,
# leaving neither working.
ENV VIRTUAL_ENV=/opt/venv
RUN python3 -m venv "$VIRTUAL_ENV"
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

WORKDIR /workspace
