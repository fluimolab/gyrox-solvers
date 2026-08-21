# Solve executor

Run `python3 -m openfoam.solve.runner` in the solver image with the `/work` request layout. The case renderer, window judge, checkpoint adapter, and CL2-derived templates are shipped together.

Cancellation waits up to `GYROX_CANCEL_WRITE_GRACE_SEC` for OpenFOAM to consume `writeNow`. It defaults to 600 seconds, matching the D-D10 solve grace; the worker's outer SIGKILL remains the final hard limit.
