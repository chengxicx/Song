# The Dockerfile can be used to create two variants
# by using "--build-arg INSTALL_EVERYTHING=[true|false]":
# - "true": with mecab and dictionary, mandarin parser (800+ MB)
# - "false": without (230 MB)
#
# e.g.  docker build --build-arg INSTALL_EVERYTHING=true -t lute3 .

# Official python base image.
FROM python:3.11-slim-bookworm

# Build args
ARG INSTALL_EVERYTHING=false

# Install base.
COPY pyproject.toml .
COPY README_PyPi.md .
COPY lute /lute
ENV PIP_ROOT_USER_ACTION=ignore
ENV FLIT_ROOT_INSTALL=1
# flit_core 4.2.0 (released 2026-10-27) added a containment check on the
# readme path, written as `resolved.startswith(directory + os.sep)`.  This
# image keeps its project directory at the filesystem root -- every COPY
# above lands in `/` -- so that comparison runs against `//` and a valid
# readme is rejected:
#   Readme path (README_PyPi.md) points outside the project directory
# Nothing else in the image changes with the pin, so hold flit/flit_core
# below 4.2 until upstream handles a root project directory.
RUN pip install "flit<4.2" "flit_core<4.2"
RUN flit install --only-deps --deps=production
COPY lute/config/config.yml.docker /lute/config/config.yml

COPY docker/Dockerfile_scripts/install_everything.sh /lute/install_all.sh
RUN chmod +x /lute/install_all.sh

RUN if [ "$INSTALL_EVERYTHING" = "true" ]; then /lute/install_all.sh; fi

EXPOSE 5001

# Start script.
COPY docker/Dockerfile_scripts/start.sh /lute/start.sh
RUN chmod +x /lute/start.sh
ENTRYPOINT ["/lute/start.sh"]
