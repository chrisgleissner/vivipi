class _EnumMeta(type):
    def __new__(mcls, name, bases, namespace):
        members = {}
        member_names = []
        base_type = object
        for base in bases:
            if base is object:
                continue
            if issubclass(base, (str, int)):
                base_type = base
                break

        cls = type(name, bases, dict(namespace))
        for key, value in list(namespace.items()):
            if key.startswith("_") or callable(value) or isinstance(value, property):
                continue
            member = base_type.__new__(cls, value) if base_type is not object else object.__new__(cls)
            if base_type is object:
                member._value_ = value
            member._name_ = key
            member._value_ = value
            setattr(cls, key, member)
            members[key] = member
            member_names.append(key)

        cls._member_map_ = members
        cls._member_names_ = tuple(member_names)
        return cls

    def __call__(cls, value):
        for member in cls._member_map_.values():
            if getattr(member, "_value_", member) == value:
                return member
        raise ValueError(value)

    def __getitem__(cls, key):
        return cls._member_map_[key]


def _enum_name(self):
    return self._name_


def _enum_value(self):
    return self._value_


def _enum_repr(self):
    return "%s.%s" % (self.__class__.__name__, self._name_)


Enum = _EnumMeta(
    "Enum",
    (object,),
    {
        "name": property(_enum_name),
        "value": property(_enum_value),
        "__repr__": _enum_repr,
    },
)

IntEnum = _EnumMeta("IntEnum", (int, Enum), {})