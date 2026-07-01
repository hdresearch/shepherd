"""Static well-formedness checks for kernel programs."""

from __future__ import annotations

from shepherd_kernel_v3_reference.kernel.program_admission import (
    KernelProgramInput,
    KernelProgramValidationError,
    PreparedKernelProgram,
    ensure_prepared_kernel_program,
)

__all__ = ["KernelProgramValidationError", "validate_kernel_program"]


def validate_kernel_program(program: KernelProgramInput) -> PreparedKernelProgram:
    """Reject malformed kernel programs before execution and return the admitted artifact."""

    return ensure_prepared_kernel_program(program)
