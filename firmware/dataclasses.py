MISSING = object()


class _Field:
    def __init__(self, *, default=MISSING, default_factory=None, init=True, repr=True):
        self.default = default
        self.default_factory = default_factory
        self.init = init
        self.repr = repr


def field(*, default=MISSING, default_factory=None, init=True, repr=True):
    return _Field(default=default, default_factory=default_factory, init=init, repr=repr)


def _field_names(cls):
    annotations = getattr(cls, "__annotations__", {})
    return tuple(annotations.keys())


def dataclass(_cls=None, *, frozen=False):
    def wrap(cls):
        annotations = getattr(cls, "__annotations__", {})
        field_specs = []
        for name in annotations:
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

        cls.__init__ = __init__
        cls.__repr__ = __repr__
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