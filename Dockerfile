FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

# Dependency layer, kept cacheable across source-only changes.
#
# `uv pip install --system .` builds and installs THIS project, which needs
# the actual source tree present (hatchling has to find src/reqguard/ to
# build the wheel) -- it cannot run before `COPY . .`. Only pyproject.toml's
# *dependency list* is available this early, so extract it with `uv pip
# compile` (which reads pyproject.toml without needing the source) and
# install from that instead. `--extra dev` matters here, not just for local
# development: this container's own interpreter is what runs each benchmark
# case's test suite (test_runner.py invokes `sys.executable -m pytest`), so
# pytest + pytest-json-report must be present in the image itself, not only
# reqguard's own runtime dependencies.
COPY pyproject.toml ./
RUN uv pip compile pyproject.toml --extra dev -o /tmp/requirements.txt && \
    uv pip install --system --no-cache -r /tmp/requirements.txt && \
    rm /tmp/requirements.txt

COPY . .
RUN uv pip install --system --no-cache --no-deps .

ENTRYPOINT ["reqguard"]
CMD ["--help"]
