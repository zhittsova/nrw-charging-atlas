# Data directory

Raw downloads, intermediate files, generated outputs, and runtime artifacts
are local and ignored by Git. The source manifests in `catalog/` identify the
inputs and licence notes. Use `scripts/project_stack.py fetch` to obtain the
public inputs and `scripts/project_stack.py seed` to rebuild the project data.

After a successful seed, `scripts/project_stack.py export` generates the five
canonical dashboard artifacts under `data/runtime/current/` plus a manifest
with source provenance, formula/bounds metadata, counts, and payload hashes.
The exporter keeps the prior complete runtime set when validation or export
fails. The normal `bootstrap` command runs this step in the required order.
