# Backend architecture

## HTTP integration decision

The backend remains framework-independent. HTTP/FastAPI integration is deferred
until deployment, authentication, and operational requirements are defined. The
phenotype application service is the current boundary for workflows; adding a
web framework before there is an application composition root or dependency
manifest would add infrastructure without an established deployment need.

Before adding HTTP, define the deployment target and authentication model, add a
backend dependency manifest with pinned framework/server versions, and create an
application composition root that injects the repository and an already-pinned
HPO ontology. The future adapter should define transport schemas and error
mapping, derive reviewer identity from authenticated context, and generate
review event IDs and timestamps server-side.
