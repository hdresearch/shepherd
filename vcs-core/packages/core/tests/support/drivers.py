"""Reusable substrate driver fixtures for integration seam tests."""

from __future__ import annotations

from vcs_core.spi import (
    CapabilitySet,
    CommandRequest,
    CommandSpec,
    Diagnostic,
    DriverContext,
    DriverIngressResult,
    DriverSchema,
    IngressRequest,
    ParamSpec,
    SubstrateDriver,
    UnsupportedRequestError,
)


class PlainCommandDriver:
    """A zero-config, non-selectable driver with one command."""

    binding = "plain"
    role = "test.plain"
    driver_id = "test.plain_driver"
    driver_version = "v1"

    @property
    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(accepts=frozenset({CommandRequest}), selectable=False)

    def describe(self) -> DriverSchema:
        return DriverSchema(
            driver_id=self.driver_id,
            driver_version=self.driver_version,
            capabilities=self.capabilities,
            commands={
                "echo": CommandSpec(
                    description="Echo a message.",
                    params={"message": ParamSpec(type="str", required=True)},
                )
            },
        )

    def prepare(self, context: DriverContext, request: IngressRequest) -> DriverIngressResult:
        del context
        if isinstance(request, CommandRequest) and request.command == "echo":
            return DriverIngressResult(
                diagnostics=(
                    Diagnostic(
                        code="plain.echo",
                        message=str(request.params["message"]),
                    ),
                )
            )
        raise UnsupportedRequestError(driver_id=self.driver_id, request_type=type(request))

    def capture_adapters(self, context: DriverContext) -> tuple[()]:
        del context
        return ()

    def validate_result(self, request: IngressRequest, result: DriverIngressResult) -> None:
        del request, result


assert isinstance(PlainCommandDriver(), SubstrateDriver)


def plain_command_driver_class() -> type[PlainCommandDriver]:
    """Return the class for dynamic-module discovery fixtures."""
    return PlainCommandDriver
