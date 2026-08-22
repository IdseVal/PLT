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

# Node 22, not 20: the `skills` CLI requires >= 22.20.0 and refuses to run on 20. Bookworm
# carries Python 3.11, which is the floor this project's backend asks for.
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

# Debian marks the system interpreter externally managed, so a project virtualenv is the
# supported way to install into it. Put it outside /workspace: a bind-mounted workspace would
# otherwise have the container's Linux venv overwrite the host's, and neither works afterwards.
ENV VIRTUAL_ENV=/opt/venv
RUN python3 -m venv "$VIRTUAL_ENV"
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

WORKDIR /workspace
