# Locked dependencies for the container image

Generated from `pyproject.toml` with hashes, for Python 3.12 (the image's Python):

    uv pip compile pyproject.toml --extra gcp --python-version 3.12 --generate-hashes -o requirements/gcp.lock
    uv pip compile pyproject.toml --extra s3  --python-version 3.12 --generate-hashes -o requirements/s3.lock

The image installs with `--require-hashes`, so a changed or substituted package fails the build.
CI regenerates both and fails if they differ from the committed files.
