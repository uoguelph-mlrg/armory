########## TF 2 Dev #################

ARG armory_version
FROM twosixarmory/tf2-base:${armory_version} AS armory-tf2-dev
COPY . /armory_dev/
RUN /opt/conda/bin/pip install /armory_dev/ --no-cache-dir
CMD tail -f /dev/null
