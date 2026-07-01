"""Private conservative reasoning for structural ``Match`` values.

This module is intentionally not a full matcher algebra.  The pre-launch
contract is a small sound proof fragment: runtime matching stays exact, while
``subset_of`` / ``equivalent_to`` / ``is_empty`` return ``Unknown`` whenever a
proof would require complement, public-kind ownership, or Boolean-domain
reasoning.
"""

from __future__ import annotations

from shepherd_runtime.effects._match_model import FieldPredicate, KindPattern, Node, Subset

_SUPPORTED_POSITIVE_FIELD_OPS = {"eq", "in", "is_none", "is_not_none"}


def match_subset(left: Node, right: Node) -> Subset:
    """Return conservative three-valued containment for two matcher trees."""
    if left == right or left.tag == "nothing" or right.tag == "all":
        return Subset.Yes

    left_empty = match_is_empty(left)
    if left_empty is Subset.Yes:
        return Subset.Yes

    if right.tag == "nothing":
        return _empty_subset_result(left_empty)

    if left.tag == "kind" and right.tag == "kind":
        return _kind_subset(left.args[0], right.args[0])  # type: ignore[arg-type]
    if left.tag == "field" and right.tag == "field":
        return _field_subset(left.args[0], right.args[0])  # type: ignore[arg-type]

    # Adding positive filters narrows a matcher.  This keeps common forms such
    # as ``Match.subtree(A).where(...) <= Match.subtree(A)`` decidable without
    # attempting a general Boolean algebra.
    if left.tag == "and" and _is_positive_atom(right):
        for term in left.args:
            result = match_subset(term, right)  # type: ignore[arg-type]
            if result is Subset.Yes:
                return Subset.Yes
        return Subset.Unknown

    # A positive atom can satisfy a positive conjunction only when every
    # conjunct is independently proven.  Field complements, negation, and
    # right-side disjunctions stay outside the launch proof fragment.
    if _is_positive_atom(left) and right.tag == "and" and _all_positive_atoms(right.args):
        result = Subset.Yes
        for term in right.args:
            result &= match_subset(left, term)  # type: ignore[arg-type]
        return result

    return Subset.Unknown


def match_equivalent(left: Node, right: Node) -> Subset:
    """Return conservative semantic equivalence."""
    return match_subset(left, right) & match_subset(right, left)


def match_is_empty(node: Node) -> Subset:
    """Return whether a matcher is provably empty in the launch fragment."""
    if node.tag == "nothing":
        return Subset.Yes
    if node.tag in {"all", "kind"}:
        return Subset.No
    if node.tag == "field":
        return Subset.Yes if _field_is_empty(node.args[0]) else Subset.Unknown  # type: ignore[arg-type]
    if node.tag == "and":
        saw_unknown = False
        terms = tuple(term for term in node.args if isinstance(term, Node))
        for term in terms:
            result = match_is_empty(term)
            if result is Subset.Yes:
                return Subset.Yes
            if result is Subset.Unknown:
                saw_unknown = True
        if _has_simple_contradiction(terms):
            return Subset.Yes
        if len(terms) == 1 and not saw_unknown:
            return Subset.No
        return Subset.Unknown
    if node.tag == "or":
        saw_unknown = False
        for term in node.args:
            result = match_is_empty(term)  # type: ignore[arg-type]
            if result is Subset.No:
                return Subset.No
            if result is Subset.Unknown:
                saw_unknown = True
        return Subset.Unknown if saw_unknown else Subset.Yes
    return Subset.Unknown


def match_is_overbroad(node: Node) -> bool:
    """Return true when an authoritative handler surface is not structurally narrow."""
    return not _has_positive_bound(node)


def kind_patterns_disjoint(left: KindPattern, right: KindPattern) -> bool:
    """Return true when two kind/class patterns are provably disjoint."""
    return _kind_disjoint(left, right)


def kind_pattern_subset(left: KindPattern, right: KindPattern) -> Subset:
    """Return conservative kind/class containment."""
    return _kind_subset(left, right)


def _empty_subset_result(emptiness: Subset) -> Subset:
    if emptiness is Subset.Yes:
        return Subset.Yes
    if emptiness is Subset.No:
        return Subset.No
    return Subset.Unknown


def _is_positive_atom(node: Node) -> bool:
    return node.tag in {"all", "nothing", "kind", "field"}


def _all_positive_atoms(values: tuple[object, ...]) -> bool:
    return all(isinstance(value, Node) and _is_positive_atom(value) for value in values)


def _has_simple_contradiction(terms: tuple[Node, ...]) -> bool:
    positives = set(terms)
    for term in terms:
        if (
            term.tag == "not"
            and isinstance(term.args[0], Node)
            and term.args[0] in positives
            and not _contains_proof_opaque(term.args[0])
        ):
            return True

    kinds = tuple(term.args[0] for term in terms if term.tag == "kind")
    for index, left in enumerate(kinds):
        for right in kinds[index + 1 :]:
            if isinstance(left, KindPattern) and isinstance(right, KindPattern) and _kind_disjoint(left, right):
                return True

    fields = tuple(term.args[0] for term in terms if term.tag == "field")
    for index, left in enumerate(fields):
        for right in fields[index + 1 :]:
            if isinstance(left, FieldPredicate) and isinstance(right, FieldPredicate) and _field_disjoint(left, right):
                return True

    return False


def _has_positive_bound(node: Node) -> bool:
    if node.tag == "nothing":
        return True
    if node.tag == "kind":
        return True
    if node.tag == "field":
        field = node.args[0]
        return isinstance(field, FieldPredicate) and field.op in _SUPPORTED_POSITIVE_FIELD_OPS
    if node.tag == "and":
        return any(isinstance(term, Node) and _has_positive_bound(term) for term in node.args)
    if node.tag == "or":
        return all(isinstance(term, Node) and _has_positive_bound(term) for term in node.args)
    return False


def _contains_proof_opaque(node: Node) -> bool:
    if node.tag == "predicate":
        return True
    return _contains_field_predicate(node)


def _contains_field_predicate(node: Node) -> bool:
    if node.tag == "field":
        return True
    return any(isinstance(arg, Node) and _contains_field_predicate(arg) for arg in node.args)


def _kind_subset(left: KindPattern, right: KindPattern) -> Subset:
    if left == right:
        return Subset.Yes
    if _same_registered_exact_kind(left, right):
        return Subset.Yes
    if _mixed_kind_domains(left, right):
        if left.mode == "exact" and right.mode == "exact" and left.kind != right.kind:
            return Subset.No
        return Subset.Unknown
    if left.cls is not None and right.cls is not None:
        return _class_kind_subset(left, right)
    return _public_kind_subset(left, right)


def _class_kind_subset(left: KindPattern, right: KindPattern) -> Subset:
    if left.mode == "exact" and right.mode == "exact":
        return Subset.Yes if left.cls is right.cls else Subset.No

    if right.mode == "subtree":
        return Subset.Yes if issubclass(left.cls, right.cls) else Subset.No  # type: ignore[arg-type]

    if right.mode == "descendants":
        if not issubclass(left.cls, right.cls):  # type: ignore[arg-type]
            return Subset.No
        if left.cls is right.cls and left.mode in {"exact", "subtree"}:
            return Subset.No
        if left.mode in {"exact", "subtree", "descendants"}:
            return Subset.Yes

    if right.mode == "exact":
        if left.mode == "exact":
            return Subset.Yes if left.cls is right.cls else Subset.No
        return Subset.Unknown

    return Subset.Unknown


def _public_kind_subset(left: KindPattern, right: KindPattern) -> Subset:
    if left.mode == "exact" and right.mode == "exact":
        return Subset.Yes if left.kind == right.kind else Subset.No

    if right.mode == "subtree":
        return Subset.Yes if _kind_matches_pattern(right, left.kind) else Subset.No

    if right.mode == "descendants":
        return Subset.Yes if _kind_matches_pattern(right, left.kind) and left.kind != right.kind else Subset.No

    if right.mode == "exact":
        if left.mode == "exact":
            return Subset.Yes if left.kind == right.kind else Subset.No
        return Subset.No

    return Subset.Unknown


def _kind_disjoint(left: KindPattern, right: KindPattern) -> bool:
    if left == right or _same_registered_exact_kind(left, right):
        return False
    if _mixed_kind_domains(left, right):
        if _has_public_kind(left) and _has_public_kind(right):
            return _public_kind_regions_disjoint(left, right)
        return False
    if left.mode == "exact":
        return not _kind_matches_pattern(right, left.kind)
    if right.mode == "exact":
        return not _kind_matches_pattern(left, right.kind)
    if left.cls is not None and right.cls is not None:
        return not (issubclass(left.cls, right.cls) or issubclass(right.cls, left.cls))
    return _public_kind_regions_disjoint(left, right)


def _same_registered_exact_kind(left: KindPattern, right: KindPattern) -> bool:
    return left.mode == "exact" and right.mode == "exact" and left.kind == right.kind


def _mixed_kind_domains(left: KindPattern, right: KindPattern) -> bool:
    return (left.cls is None) != (right.cls is None)


def _public_kind_regions_disjoint(left: KindPattern, right: KindPattern) -> bool:
    return not (_kind_root_contains(left.kind, right.kind) or _kind_root_contains(right.kind, left.kind))


def _kind_matches_pattern(pattern: KindPattern, kind: str) -> bool:
    if pattern.mode == "exact":
        return kind == pattern.kind
    if pattern.mode == "subtree":
        return kind == pattern.kind or kind.startswith(f"{pattern.kind}.")
    return kind.startswith(f"{pattern.kind}.") and kind != pattern.kind


def _kind_root_contains(parent: str, child: str) -> bool:
    return child == parent or child.startswith(f"{parent}.")


def _has_public_kind(pattern: KindPattern) -> bool:
    return not pattern.kind.startswith("local.")


def _field_subset(left: FieldPredicate, right: FieldPredicate) -> Subset:
    if left == right:
        return Subset.Yes
    if _field_is_empty(left):
        return Subset.Yes
    if left.name != right.name:
        return Subset.Unknown
    if left.op not in _SUPPORTED_POSITIVE_FIELD_OPS or right.op not in _SUPPORTED_POSITIVE_FIELD_OPS:
        return Subset.Unknown
    if left.op == "eq" and right.op == "eq":
        return Subset.Yes if left.value == right.value else Subset.No
    if left.op == "eq" and right.op == "in":
        return Subset.Yes if left.value in right.value else Subset.No
    if left.op == "in" and right.op == "in":
        return Subset.Yes if set(left.value).issubset(set(right.value)) else Subset.No
    if left.op == "in" and right.op == "eq":
        values = set(left.value)
        return Subset.Yes if values.issubset({right.value}) else Subset.No
    if right.op == "is_none":
        return _field_subset_is_none(left)
    if right.op == "is_not_none":
        return _field_subset_is_not_none(left)
    return Subset.Unknown


def _field_subset_is_none(left: FieldPredicate) -> Subset:
    if left.op == "eq":
        return Subset.Yes if left.value is None else Subset.No
    if left.op == "in":
        values = set(left.value)
        return Subset.Yes if values and values.issubset({None}) else Subset.No
    if left.op == "is_none":
        return Subset.Yes
    if left.op == "is_not_none":
        return Subset.No
    return Subset.Unknown


def _field_subset_is_not_none(left: FieldPredicate) -> Subset:
    if left.op == "eq":
        return Subset.No if left.value is None else Subset.Yes
    if left.op == "in":
        values = set(left.value)
        return Subset.Yes if values and None not in values else Subset.No
    if left.op == "is_not_none":
        return Subset.Yes
    if left.op == "is_none":
        return Subset.No
    return Subset.Unknown


def _field_disjoint(left: FieldPredicate, right: FieldPredicate) -> bool:
    if left.name != right.name:
        return False
    if left.op == "eq" and right.op == "eq":
        return left.value != right.value
    if left.op == "eq" and right.op == "in":
        return left.value not in right.value
    if left.op == "in" and right.op == "eq":
        return right.value not in left.value
    if left.op == "in" and right.op == "in":
        return set(left.value).isdisjoint(set(right.value))
    if left.op == "is_none":
        return _field_subset_is_not_none(right) is Subset.Yes
    if right.op == "is_none":
        return _field_subset_is_not_none(left) is Subset.Yes
    if left.op == "is_not_none":
        return _field_subset_is_none(right) is Subset.Yes
    if right.op == "is_not_none":
        return _field_subset_is_none(left) is Subset.Yes
    return False


def _field_is_empty(field: FieldPredicate) -> bool:
    return field.op == "in" and not field.value
