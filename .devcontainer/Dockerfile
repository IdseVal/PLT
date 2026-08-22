# The reproducible toolchain every agent runs inside.
#
# Two jobs, and deliberately not a third. It pins the toolchain so a test that passes here
# passes on any machine, and it is the shape this project deploys in when it stops running on
# a laptop. It is **not** a sandbox: the container is not a security boundary, and nothing
# here should be read as containment. See docs/agent-workflow-setup.md, "Isolation".
#
# Orca is not installed. It is a desktop application that runs on the host, dispatches agents
# and owns the worktrees; it cannot live inside the environment it dispatches into. Trying to
# `npm install -g` it fails, because it is not published to npm at all.

# Bookworm carries Python 3.11, which is the floor this project's backend asks for.
FROM mcr.microsoft.com/devcontainers/typescript-node:1-22-bookworm

# git and gh: the daily digest is derived from commit and pull-request history rather than
# from anything an agent remembers to write down, so the container needs to read both.
# jq keeps shell-side JSON handling out of Python.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        gh \
        jq \
        curl \
        python3 \
        python3-pip \
        python3-venv \
    && rm -rf /var/lib/apt/lists/*

# The `1-22` tag ships Node 22.16 today, which is *below* the 22.20.0 floor the `skills` CLI
# declares. npm currently lets it run anyway with an EBADENGINE warning, and a toolchain that
# works by warning is one that breaks the day npm decides to enforce it. `n` pins the
# interpreter above the floor whatever the base image moves to.
#
# Nothing is chained onto this. `n` replaces /usr/local/bin/node underneath the running shell,
# so a subsequent npm in the same RUN resolves to the binary that has just been swapped out --
# which is what made an earlier `&& npm install -g npm@latest` here fail the build. The npm
# that comes with the pinned Node is the one to use.
RUN npm install -g n && n 22.20.0

# Debian marks the system interpreter externally managed, so a project virtualenv is the
# supported way to install into it. Put it outside /workspace: a bind-mounted workspace would
# otherwise have the container's Linux venv overwrite the host's, and neither works afterwards.
ENV VIRTUAL_ENV=/opt/venv
RUN python3 -m venv "$VIRTUAL_ENV"
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

WORKDIR /workspace
