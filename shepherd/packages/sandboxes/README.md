# shepherd-sandboxes

Optional remote sandbox integrations for Shepherd.

This package contains vendor-specific sandbox wrappers such as:

- `shepherd_sandboxes.daytona.DaytonaSandbox`
- `shepherd_sandboxes.e2b.E2BSandbox`
- `shepherd_sandboxes.kubernetes.K8sSandbox`
- `shepherd_sandboxes.modal.ModalSandbox`
- `shepherd_sandboxes.prime.PrimeSandbox`

These modules import vendor SDKs lazily. Install the relevant SDK before using a
given backend.
