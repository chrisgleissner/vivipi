import sys


MISSING = object()


class _Field:
    def __init__(self, *, default=MISSING, default_factory=None, init=True, repr=True):
        self.default = default
        self.default_factory = default_factory
        self.init = init
        self.repr = repr


def field(*, default=MISSING, default_factory=None, init=True, repr=True):
    return _Field(default=default, default_factory=default_factory, init=init, repr=repr)


def _is_identifier(value):
    if not value:
        return False
    first = value[0]
    if first != "_" and not ("A" <= first <= "Z" or "a" <= first <= "z"):
        return False
    for character in value[1:]:
        if character == "_":
            continue
        if "A" <= character <= "Z" or "a" <= character <= "z" or "0" <= character <= "9":
            continue
        return False
    return True


def _field_names_from_source(cls):
    module = sys.modules.get(getattr(cls, "__module__", ""))
    module_path = getattr(module, "__file__", None)
    if not module_path:
        return ()

    try:
        with open(module_path, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        return ()

    class_name = cls.__name__
    class_indent = None
    body_indent = None
    within_class = False
    field_names = []

    for line in lines:
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())

        if not within_class:
            if stripped.startswith("class %s" % class_name):
                within_class = True
                class_indent = indent
            continue

        if stripped and indent <= class_indent:
            break
        if not stripped:
            continue
        if body_indent is None and indent > class_indent:
            body_indent = indent
        if indent != body_indent:
            continue
        if stripped.startswith("def ") or stripped.startswith("class ") or stripped.startswith("@"):
            continue

        separator = stripped.find(":")
        if separator <= 0:
            continue

        name = stripped[:separator].strip()
        if _is_identifier(name):
            field_names.append(name)

    return tuple(field_names)


def _field_names(cls):
    annotations = getattr(cls, "__annotations__", {})
    if annotations:
        return tuple(annotations.keys())
    return _field_names_from_source(cls)


def dataclass(_cls=None, *, frozen=False):
    def wrap(cls):
        field_specs = []
        for name in _field_names(cls):
            raw_value = getattr(cls, name, MISSING)
            if isinstance(raw_value, _Field):
                spec = raw_value
            elif raw_value is MISSING:
                spec = _Field()
            else:
                spec = _Field(default=raw_value)
            field_specs.append((name, spec))

        def __init__(self, *args, **kwargs):
            init_fields = [item for item in field_specs if item[1].init]
            if len(args) > len(init_fields):
                raise TypeError("too many positional arguments")

            consumed = set()
            for index, (name, spec) in enumerate(init_fields):
                if index < len(args):
                    value = args[index]
                    consumed.add(name)
                elif name in kwargs:
                    value = kwargs[name]
                    consumed.add(name)
                elif spec.default is not MISSING:
                    value = spec.default
                elif spec.default_factory is not None:
                    value = spec.default_factory()
                else:
                    raise TypeError("missing required argument: %s" % name)
                object.__setattr__(self, name, value)

            for key, value in kwargs.items():
                if key in consumed:
                    continue
                object.__setattr__(self, key, value)

            for name, spec in field_specs:
                if spec.init:
                    continue
                if spec.default is not MISSING:
                    value = spec.default
                elif spec.default_factory is not None:
                    value = spec.default_factory()
                else:
                    value = None
                object.__setattr__(self, name, value)

            post_init = getattr(self, "__post_init__", None)
            if post_init is not None:
                post_init()

        def __repr__(self):
            parts = []
            for name, spec in field_specs:
                if spec.repr:
                    parts.append("%s=%r" % (name, getattr(self, name)))
            return "%s(%s)" % (cls.__name__, ", ".join(parts))

        def __eq__(self, other):
            if other.__class__ is not cls:
                return False
            for name, _spec in field_specs:
                if getattr(self, name) != getattr(other, name):
                    return False
            return True

        cls.__init__ = __init__
        cls.__repr__ = __repr__
        cls.__eq__ = __eq__
        cls.__dataclass_fields__ = tuple(field_specs)

        if frozen:
            def __setattr__(self, name, value):
                raise AttributeError("cannot assign to field")

            cls.__setattr__ = __setattr__

        return cls

    if _cls is None:
        return wrap
    return wrap(_cls)


def replace(instance, **changes):
    values = {}
    for name, spec in getattr(instance.__class__, "__dataclass_fields__", ()):  # pragma: no branch - tiny shim
        if not spec.init:
            continue
        values[name] = changes.pop(name, getattr(instance, name))
    if changes:
        raise TypeError("unexpected fields: %s" % ", ".join(sorted(changes)))
    return instance.__class__(**values)