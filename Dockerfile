# syntax=docker/dockerfile:1.7
FROM opencfd/openfoam-default:2512@sha256:33fb575aa9980d2bc42fd58c75ae698c489293ba30c991380fe3f899c622f319 AS base

USER root
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3-pip \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m pip install --break-system-packages --no-cache-dir \
       numpy==2.5.1 jsonschema==4.26.0 pytest==8.4.2

FROM base AS case
WORKDIR /opt/solvers
ENV PYTHONPATH=/opt/solvers \
    GYROX_SCHEMA_DIR=/opt/solvers/contract/schemas \
    GYROX_SOURCE_ROOT=/opt/gyrox-m0

# Contract and executable case layers are separate from the pinned runtime layer.
COPY contract/ /opt/solvers/contract/
COPY openfoam/ /opt/solvers/openfoam/

# Named context `gyrox` supplies immutable M0 acceptance records to the test image.
COPY --from=gyrox m0/fixtures/gyroid-30-4-0.4/labels.vti /opt/gyrox-m0/m0/fixtures/gyroid-30-4-0.4/labels.vti
COPY --from=gyrox m0/m04/runs/phaseC/mesh/CL2/patch-map.json /opt/gyrox-m0/m0/m04/runs/phaseC/mesh/CL2/patch-map.json
COPY --from=gyrox m0/m04/runs/phaseC/results/window-analysis-C.json /opt/gyrox-m0/m0/m04/runs/phaseC/results/window-analysis-C.json
COPY --from=gyrox m0/m04/runs/phaseC/results/extract/CL2/timeseries.csv /opt/gyrox-m0/m0/m04/runs/phaseC/results/extract/CL2/timeseries.csv
COPY --from=gyrox m0/m04/runs/phaseC/solve/CL2/constant/hot/thermophysicalProperties /opt/gyrox-m0/m0/m04/runs/phaseC/solve/CL2/constant/hot/thermophysicalProperties
COPY --from=gyrox m0/m04/runs/phaseC/solve/CL2/constant/cold/thermophysicalProperties /opt/gyrox-m0/m0/m04/runs/phaseC/solve/CL2/constant/cold/thermophysicalProperties
COPY --from=gyrox m0/m04/runs/phaseC/solve/CL2/constant/solid/thermophysicalProperties /opt/gyrox-m0/m0/m04/runs/phaseC/solve/CL2/constant/solid/thermophysicalProperties

RUN ln -s /opt/solvers/openfoam /opt/solvers/tests \
    && python3 -c 'import platform; assert platform.python_version() == "3.12.3"'

FROM case AS runtime
CMD ["python3", "-m", "openfoam.solve.runner"]
