#!/usr/bin/env bash
#
# Everything the container needs that the image cannot bake in: project dependencies, which
# change with the lockfiles, and the agent skills, which are fetched from GitHub.
#
# Deliberately NOT `set -e` around the whole file. A failed skill fetch is a network problem
# and must not leave a developer with no container at all; a failed dependency install must.
# The two are treated differently below, and the script says which happened.
set -uo pipefail

cd /workspace

echo "==> Python dependencies"
set -e
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -e "backend[dev]" -c backend/constraints.txt
set +e

echo "==> Node dependencies"
if [ -f frontend/package-lock.json ]; then
  (cd frontend && npm ci --no-audit --no-fund) || {
    echo "!! npm ci failed. The container is usable; run it by hand in frontend/." >&2
  }
fi

echo "==> Agent skills"
if bash .orca/setup_skills.sh; then
  echo "    skills installed"
else
  echo "!! Skill installation failed — usually a network or GitHub rate-limit problem." >&2
  echo "   The container is otherwise ready. Re-run: bash .orca/setup_skills.sh" >&2
fi

echo
echo "Ready. The corpus mount is $([ -d /corpus ] && echo 'present at /corpus' || echo 'ABSENT — set PLT_CORPUS_HOST_DIR and rebuild to run the pipeline')."
