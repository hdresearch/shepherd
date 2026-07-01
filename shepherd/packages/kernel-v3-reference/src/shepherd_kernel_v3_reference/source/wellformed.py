"""Source well-formedness checks for the §02 core fragment."""

from __future__ import annotations

from shepherd_kernel_v3_reference.source.syntax import (
    Abort,
    Computation,
    Handle,
    Let,
    Perform,
    Resume,
    Return,
)


class SourceFormError(RuntimeError):
    """Raised when source syntax falls outside the implemented core fragment."""


def validate_core_program(term: Computation) -> None:
    """Validate an ordinary Core-A source computation.

    The standalone source program is not a selected handler body, so
    handler-local forms such as `Resume` and `Abort` are rejected.
    """

    _validate(
        term,
        in_handler_body=False,
        answer_position=False,
        allow_publication_controls=False,
    )


def validate_core_handler_body(term: Computation) -> None:
    """Validate a selected Core-A handler body.

    `Abort(value)` is admitted only in answer position with respect to the
    active handler boundary. A preceding `Let` is allowed, but `Abort` may not
    appear in the bound computation of a `Let`.
    """

    _validate(
        term,
        in_handler_body=True,
        answer_position=True,
        allow_publication_controls=False,
    )


def validate_publication_experimental_program(term: object) -> None:
    """Validate a publication-experimental source computation."""

    _validate(
        term,
        in_handler_body=False,
        answer_position=False,
        allow_publication_controls=True,
    )


def validate_publication_experimental_handler_body(term: object) -> None:
    """Validate a selected publication-experimental handler body."""

    _validate(
        term,
        in_handler_body=True,
        answer_position=True,
        allow_publication_controls=True,
    )


validate_program = validate_core_program
validate_handler_body = validate_core_handler_body


def _validate(
    term: object,
    *,
    in_handler_body: bool,
    answer_position: bool,
    allow_publication_controls: bool,
) -> None:
    from shepherd_kernel_v3_reference.source.experimental import Forward, TerminalDelay, TerminalFork

    work = [(term, in_handler_body, answer_position)]
    while work:
        current, current_in_handler_body, current_answer_position = work.pop()
        match current:
            case Return():
                continue

            case Perform():
                continue

            case Resume():
                if not current_in_handler_body:
                    raise SourceFormError("Resume(value) used outside any handler body")
                continue

            case Abort():
                if not current_in_handler_body:
                    raise SourceFormError("Abort(value) used outside any handler body")
                if not current_answer_position:
                    raise SourceFormError("Abort(value) is valid only in handler answer position")
                continue

            case Forward() | TerminalDelay() | TerminalFork():
                if not allow_publication_controls:
                    raise SourceFormError(f"{type(current).__name__} requires the publication experimental profile")
                if not current_in_handler_body:
                    raise SourceFormError(f"{type(current).__name__} used outside any handler body")
                if not current_answer_position:
                    raise SourceFormError(f"{type(current).__name__} is valid only in handler answer position")
                continue

            case Let(bound=bound, body=body):
                work.append((body, current_in_handler_body, current_answer_position))
                work.append((bound, current_in_handler_body, False))
                continue

            case Handle(body=body):
                # A nested Handle evaluates an ordinary handled computation.
                # Handler-local forms are not in scope in the selected worker.
                work.append((body, False, False))
                continue

            case _:
                raise TypeError(f"unknown computation form: {current!r}")
