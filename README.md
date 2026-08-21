# Gyrox Solvers

Gyrox Solvers is the public GPL repository for the isolated OpenFOAM mesh and solve image used by Gyrox.

The product repository consumes this repository only as a digest-pinned container image. Source-level communication is limited to the checked-in, MIT-licensed `contract/` tree. R15 is one-way: neither repository receives credentials for the other, and solver product code never becomes a source dependency of the main repository.

Repository code is GPL-3.0-only except `contract/` and `ci/`, which carry the MIT terms stated in `LICENSE`.

Run the product-image test suite with an explicit source worktree: `GYROX_ROOT=/absolute/path/to/gyrox make test IMAGE=sha256:<full-image-id>`.
