# shepherd-export

Public export and trajectory interchange surfaces for the Shepherd framework.

This package is the stable owner path for:

- JSON summary export/import
- lossless trajectory export/import
- optional ATIF/Harbor interop

During Phase 0 boundary hardening, the implementation is still delegated through
`shepherd-core` compatibility modules so the existing `shepherd_core` surface stays
import-stable while the facade reroute completes.
