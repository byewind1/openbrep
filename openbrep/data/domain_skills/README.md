# Built-in GDL domain Skill packages

These packages are versioned, data-only contracts. OpenBrep validates and
selects their observation schemas, Plan policies, fixture provenance, and
framework check IDs. It never executes code shipped inside a Skill package.

The cabinet and lattice-window packages are currently **development
scaffolds**. Their synthetic fixtures test typed field handling, canonical
units, and preservation of unknown observations. They do not claim architectural
truth, recognition accuracy, or production readiness. A domain maintainer must
review construction rules, add licensed positive/negative examples with signed
off expectations, and promote a package to `verified` before publishing quality
claims. Terminal users' private images are runtime inputs and do not enter these
packages or public benchmark data.

The `manifest.json` schema is versioned independently from prompt Skills in the
top-level `skills/` folder. Manifest paths are package-relative and hashes are
checked before fixture use. Only checks already registered by OpenBrep may be
named in requirements.
