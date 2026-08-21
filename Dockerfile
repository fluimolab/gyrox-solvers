# syntax=docker/dockerfile:1.7
ARG BASE_IMAGE=gyrox/solvers:local
FROM opencfd/openfoam-default:2512@sha256:33fb575aa9980d2bc42fd58c75ae698c489293ba30c991380fe3f899c622f319 AS base

USER root
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3-pip \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m pip install --break-system-packages --no-cache-dir \
       numpy==2.5.1 jsonschema==4.26.0

FROM base AS source
WORKDIR /opt/solvers
ENV PYTHONPATH=/opt/solvers \
    GYROX_CONTRACT_ROOT=/opt/solvers/contract

COPY contract/ /opt/solvers/contract/
COPY openfoam/ /opt/solvers/openfoam/

FROM source AS runtime
# Runtime carries contract + product modules, but no pytest suite or M0 assets.
RUN find /opt/solvers/openfoam -type d -name tests -prune -exec rm -rf '{}' + \
    && find /opt/solvers/openfoam -type d -name __pycache__ -prune -exec rm -rf '{}' +
RUN python3 -c 'import platform; assert platform.python_version() == "3.12.3"'
CMD ["python3", "-m", "openfoam.solve.runner"]

FROM source AS test-ci
RUN python3 -m pip install --break-system-packages --no-cache-dir pytest==8.4.2
COPY pytest.ini /opt/solvers/pytest.ini
RUN ln -s /opt/solvers/openfoam /opt/solvers/tests

FROM ${BASE_IMAGE} AS test
USER root
WORKDIR /opt/solvers
RUN python3 -m pip install --break-system-packages --no-cache-dir pytest==8.4.2
COPY pytest.ini /opt/solvers/pytest.ini
COPY openfoam/mesh/tests/ /opt/solvers/openfoam/mesh/tests/
COPY openfoam/solve/tests/ /opt/solvers/openfoam/solve/tests/
RUN ln -s /opt/solvers/openfoam /opt/solvers/tests
ENV GYROX_SOURCE_ROOT=/opt/gyrox-m0
# Local-only named context supplies immutable M0 acceptance records.
COPY --from=gyrox m0/fixtures/gyroid-30-4-0.4/labels.vti /opt/gyrox-m0/m0/fixtures/gyroid-30-4-0.4/labels.vti
COPY --from=gyrox m0/m04/runs/phaseC/mesh/CL2/patch-map.json /opt/gyrox-m0/m0/m04/runs/phaseC/mesh/CL2/patch-map.json
COPY --from=gyrox m0/m04/runs/phaseC/results/window-analysis-C.json /opt/gyrox-m0/m0/m04/runs/phaseC/results/window-analysis-C.json
COPY --from=gyrox m0/m04/runs/phaseC/results/extract/CL2/timeseries.csv /opt/gyrox-m0/m0/m04/runs/phaseC/results/extract/CL2/timeseries.csv
COPY --from=gyrox m0/m04/runs/phaseC/solve/CL2/constant/hot/thermophysicalProperties /opt/gyrox-m0/m0/m04/runs/phaseC/solve/CL2/constant/hot/thermophysicalProperties
COPY --from=gyrox m0/m04/runs/phaseC/solve/CL2/constant/cold/thermophysicalProperties /opt/gyrox-m0/m0/m04/runs/phaseC/solve/CL2/constant/cold/thermophysicalProperties
COPY --from=gyrox m0/m04/runs/phaseC/solve/CL2/constant/solid/thermophysicalProperties /opt/gyrox-m0/m0/m04/runs/phaseC/solve/CL2/constant/solid/thermophysicalProperties
