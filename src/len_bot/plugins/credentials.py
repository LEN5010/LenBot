"""Which plugin configuration leaves are credentials, and how a save keeps them.

A plugin's declared JSON Schema is the only description of its parameters, so
it is also the only place that can say which leaves are secrets.  Scanning it
by path — not just the first level — is what keeps a credential nested inside
a backend object (for example the Gateway service token) out of a list or save
response.  The same walk is used on the way back in, so a request that edits a
non-secret field next to a credential does not silently erase it.

The names here describe what the project already uses.  There is deliberately
no separate secret store and no second source of truth: the root file stays
the one place a credential lives.
"""
from __future__ import annotations

from typing import Any, Iterable

# Field names the project already treats as credentials.  Matching is by the
# leaf's own name, so a nested ``gateway.token`` is found by the same rule as a
# top-level ``sessdata``.
CREDENTIAL_NAMES = frozenset({
    'sessdata', 'bili_jct', 'api_key', 'access_token', 'refresh_token',
    'token', 'password', 'secret', 'cookie', 'authorization',
})


def _resolve(schema: dict, root: dict, seen: frozenset[str] = frozenset()) -> dict:
    """Follow a local ``$ref`` to its definition, refusing an unbreakable loop."""
    reference = schema.get('$ref')
    if not isinstance(reference, str) or not reference.startswith('#/'):
        return schema
    if reference in seen:
        return {}
    target: Any = root
    for part in reference[2:].split('/'):
        if not isinstance(target, dict) or part not in target:
            return {}
        target = target[part]
    if not isinstance(target, dict):
        return {}
    return _resolve(target, root, seen | {reference})


def _is_credential(name: str, field: dict) -> bool:
    return (name.lower() in CREDENTIAL_NAMES
            or bool(field.get('writeOnly')) or field.get('format') == 'password')


def _branches(field: dict, root: dict) -> list[dict]:
    """The concrete shapes one field may take, with local refs resolved."""
    for key in ('anyOf', 'oneOf'):
        options = field.get(key)
        if isinstance(options, list):
            return [_branches(_resolve(option, root), root)[0] for option in options]
    return [field]


def sensitive_paths(schema: dict, *, prefix: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
    """Every credential leaf in a declared configuration schema, by path.

    Objects and their nested objects are walked, ``$defs`` references are
    followed, and lists are walked through their item schema — a credential in
    a list element is the same secret as one in a fixed field.
    """
    root = schema if isinstance(schema, dict) else {}
    found: list[tuple[str, ...]] = []

    def walk(field: dict, path: tuple[str, ...]) -> None:
        field = _resolve(field, root)
        if not field:
            return
        if 'items' in field:
            walk(field['items'], path)
        properties = field.get('properties')
        if isinstance(properties, dict):
            for name, child in properties.items():
                walk(child, path + (name,))
        for option in list(field.get('anyOf') or []) + list(field.get('oneOf') or []):
            walk(_resolve(option, root), path)
        if len(path) > len(prefix) and _is_credential(path[-1], field):
            found.append(path)

    for name, child in (root.get('properties') or {}).items():
        walk(child, prefix + (name,))
    return found


def _public_leaf(value: Any, path: tuple[str, ...], secrets: set[tuple[str, ...]],
                 set_paths: dict[str, bool]) -> Any:
    """One value with every credential leaf removed and remembered as set."""
    if isinstance(value, dict):
        result = {}
        for name, item in value.items():
            child = path + (name,)
            if child in secrets:
                set_paths['.'.join(child)] = bool(item)
                continue
            result[name] = _public_leaf(item, child, secrets, set_paths)
        return result
    if isinstance(value, list):
        return [_public_leaf(item, path, secrets, set_paths) for item in value]
    return value


def public_config(config: Any, schema: dict) -> tuple[Any, dict[str, bool], list[str]]:
    """A configuration safe to return, its credential state, and secret paths.

    The returned configuration carries no credential value at any depth; the
    credential state says only whether one is set.  Callers that must scrub a
    message use ``secret_paths`` to find the leaves they were told to hide.
    """
    secrets = set(sensitive_paths(schema))
    set_paths: dict[str, bool] = {}
    for path in sorted(secrets):
        if len(path) == 1:
            set_paths[path[0]] = bool(isinstance(config, dict) and config.get(path[0]))
    if not isinstance(config, dict):
        return config, set_paths, ['.'.join(path) for path in sorted(secrets)]
    return (_public_leaf(config, (), secrets, set_paths), set_paths,
            ['.'.join(path) for path in sorted(secrets)])


def secret_values(config: Any, schema: dict) -> list[str]:
    """The actual credential strings present in a configuration.

    Used only to scrub a message that may have quoted one; the values never
    travel with the public projection.
    """
    values: list[str] = []

    def walk(value: Any, path: tuple[str, ...]) -> None:
        if isinstance(value, dict):
            for name, item in value.items():
                child = path + (name,)
                if child in secrets:
                    if isinstance(item, str) and item:
                        values.append(item)
                    continue
                walk(item, child)
        elif isinstance(value, list):
            for item in value:
                walk(item, path)

    secrets = set(sensitive_paths(schema))
    walk(config, ())
    return values


def merge_config(existing: Any, incoming: Any, schema: dict) -> Any:
    """Apply a save request over the stored configuration, keeping credentials.

    A credential the request omits or leaves empty keeps its stored value: the
    panel cannot read one back, so its absence in a round trip is not a request
    to delete it.  A non-empty value replaces it.  An explicit ``null`` on a
    credential is a deliberate clear and removes it.  Everything that is not a
    credential is replaced by what the request says, with nested objects merged
    so editing one Gateway field does not drop its siblings.
    """
    secrets = set(sensitive_paths(schema))

    def merge(old: Any, new: Any, path: tuple[str, ...]) -> Any:
        if not isinstance(old, dict) or not isinstance(new, dict):
            return new
        result = dict(old)
        for name, item in new.items():
            child = path + (name,)
            if child in secrets:
                if item is None:
                    result.pop(name, None)
                elif isinstance(item, str) and not item:
                    continue
                else:
                    result[name] = item
                continue
            result[name] = merge(old.get(name), item, child) if name in old else item
        return result

    if not isinstance(existing, dict) or not isinstance(incoming, dict):
        return incoming
    return merge(existing, incoming, ())


def schema_secret_fields(schema: dict, secret_paths: set[tuple[str, ...]]) -> list[dict]:
    """The schema nodes that describe a credential leaf, so a caller can mark them.

    Only the leaf's own declaration is returned — the leaf inside a nested
    object or a list element, not the object that contains it — and a leaf
    inside a compound field that is otherwise edited as JSON is skipped: its
    rendering is the JSON draft's, not a field's.
    """
    root = schema if isinstance(schema, dict) else {}
    found: list[dict] = []

    def top_level_only(path: tuple[str, ...]) -> bool:
        return len(path) == 1 and bool(root.get('properties', {}).get(path[0], {}).get('type')) \
            and root['properties'][path[0]]['type'] in {'string', 'integer', 'number', 'boolean'}

    def walk(field: dict, path: tuple[str, ...]) -> None:
        field = _resolve(field, root)
        if not field:
            return
        properties = field.get('properties')
        if isinstance(properties, dict):
            for name, child in properties.items():
                child_path = path + (name,)
                if child_path in secret_paths and top_level_only(child_path):
                    found.append(properties[name])
                    continue
                walk(child, child_path)
        for option in list(field.get('anyOf') or []) + list(field.get('oneOf') or []):
            walk(_resolve(option, root), path)
        if 'items' in field:
            walk(field['items'], path)

    for name, child in (root.get('properties') or {}).items():
        walk(child, (name,))
    return found


def schema_of(spec) -> dict:
    """The declared configuration schema, or an empty one when there is none."""
    model = getattr(spec, 'config_model', None)
    try:
        return model.model_json_schema()
    except AttributeError:
        return {}


def paths_of(paths: Iterable[str]) -> set[tuple[str, ...]]:
    """Rebuild path tuples from their dotted form."""
    return {tuple(path.split('.')) for path in paths}
