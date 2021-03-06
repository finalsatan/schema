__version__ = '0.4.2'
__all__ = ['Schema', 'And', 'Or', 'Optional', 'SchemaError']


class SchemaError(Exception):

    """Error during Schema validation."""

    def __init__(self, autos, errors, missing_keys=None, invalid_keys=None,
                 wrong_keys=None):
        self.autos = autos if type(autos) is list else [autos]
        self.errors = errors if type(errors) is list else [errors]
        self.missing_keys = missing_keys
        self.invalid_keys = invalid_keys
        self.wrong_keys = wrong_keys
        Exception.__init__(self, self.code)

    @property
    def code(self):
        def uniq(seq):
            seen = set()
            seen_add = seen.add
            # This way removes duplicates while preserving the order.
            return [x for x in seq if x not in seen and not seen_add(x)]
        a = uniq(i for i in self.autos if i is not None)
        e = uniq(i for i in self.errors if i is not None)
        if e:
            return '\n'.join(e)
        return '\n'.join(a)


class And(object):

    def __init__(self, *args, **kw):
        self._args = args
        assert list(kw) in (['error'], ['allow_wrong_keys'], [])
        self._error = kw.get('error')
        self._allow_wrong_keys = kw.get('allow_wrong_keys')

    def __repr__(self):
        return '%s(%s)' % (self.__class__.__name__,
                           ', '.join(repr(a) for a in self._args))

    def validate(self, data):
        for s in [Schema(s, error=self._error, allow_wrong_keys=self._allow_wrong_keys) for s in self._args]:
            data = s.validate(data)
        return data


class Or(And):

    def validate(self, data):
        x = SchemaError([], [])
        for s in [Schema(s, error=self._error, allow_wrong_keys=self._allow_wrong_keys) for s in self._args]:
            try:
                return s.validate(data)
            except SchemaError as _x:
                x = _x
        raise SchemaError(['%r did not validate %r' % (self, data)] + x.autos,
                          [self._error] + x.errors)


class Use(object):

    def __init__(self, callable_, error=None):
        assert callable(callable_)
        self._callable = callable_
        self._error = error

    def __repr__(self):
        return '%s(%r)' % (self.__class__.__name__, self._callable)

    def validate(self, data):
        try:
            return self._callable(data)
        except SchemaError as x:
            raise SchemaError([None] + x.autos, [self._error] + x.errors)
        except BaseException as x:
            f = _callable_str(self._callable)
            raise SchemaError('%s(%r) raised %r' % (f, data, x), self._error)


COMPARABLE, CALLABLE, VALIDATOR, TYPE, DICT, ITERABLE = range(6)


def _priority(s):
    """Return priority for a given object."""
    if type(s) in (list, tuple, set, frozenset):
        return ITERABLE
    if type(s) is dict:
        return DICT
    if issubclass(type(s), type):
        return TYPE
    if hasattr(s, 'validate'):
        return VALIDATOR
    if callable(s):
        return CALLABLE
    else:
        return COMPARABLE


class Schema(object):

    def __init__(self, schema, error=None, allow_wrong_keys=True):
        self._schema = schema
        self._error = error
        self._allow_wrong_keys = allow_wrong_keys

    def __repr__(self):
        return '%s(%r)' % (self.__class__.__name__, self._schema)

    @staticmethod
    def _dict_key_priority(s):
        """Return priority for a given key object."""
        if isinstance(s, Optional):
            return _priority(s._schema) + 0.5
        return _priority(s)

    def validate(self, data):
        s = self._schema
        e = self._error
        w = self._allow_wrong_keys
        flavor = _priority(s)
        if flavor == ITERABLE:
            data = Schema(type(s), error=e, allow_wrong_keys=w).validate(data)
            o = Or(*s, error=e)
            return type(data)(o.validate(d) for d in data)
        if flavor == DICT:
            data = Schema(dict, error=e, allow_wrong_keys=w).validate(data)
            new = type(data)()  # new - is a dict of the validated values
            x = None
            coverage = set()  # matched schema keys
            # for each key and value find a schema entry matching them, if any
            sorted_skeys = sorted(s, key=self._dict_key_priority)
            for key, value in data.items():
                valid = False
                skey = None
                for skey in sorted_skeys:
                    svalue = s[skey]
                    try:
                        nkey = Schema(skey, error=e, allow_wrong_keys=w).validate(key)
                    except SchemaError:
                        pass
                    else:
                        try:
                            nvalue = Schema(svalue, error=e, allow_wrong_keys=w).validate(value)
                        except SchemaError as _x:
                            x = _x
                            x.invalid_keys = [key]
                            raise
                        else:
                            coverage.add(skey)
                            valid = True
                            break
                if valid:
                    new[nkey] = nvalue
                elif skey is not None:
                    if x is not None:
                        raise SchemaError(['Invalid value for key %r' % key] +
                                          x.autos, [e] + x.errors,
                                          invalid_keys=[key])
            required = set(k for k in s if type(k) is not Optional)
            if not required.issubset(coverage):
                missing_keys = required - coverage
                s_missing_keys = ", ".join(repr(k) for k in missing_keys)
                raise SchemaError('Missing keys: ' + s_missing_keys, e,
                                  missing_keys=list(missing_keys))
            if not self._allow_wrong_keys and len(new) != len(data):
                wrong_keys = set(data.keys()) - set(new.keys())
                wrong_keys = [k for k in sorted(wrong_keys, key=repr)]
                s_wrong_keys = ', '.join(repr(k) for k in wrong_keys)
                raise SchemaError('Wrong keys %s in %r' % (s_wrong_keys, data),
                                  e, wrong_keys=list(wrong_keys))

            # Apply default-having optionals that haven't been used:
            defaults = set(k for k in s if type(k) is Optional and
                           hasattr(k, 'default')) - coverage
            for default in defaults:
                new[default.key] = default.default

            return new
        if flavor == TYPE:
            if isinstance(data, s):
                return data
            else:
                raise SchemaError('%r should be instance of %r' %
                                  (data, s.__name__), e)
        if flavor == VALIDATOR:
            try:
                return s.validate(data)
            except SchemaError as x:
                raise SchemaError([None] + x.autos, [e] + x.errors)
            except BaseException as x:
                raise SchemaError('%r.validate(%r) raised %r' % (s, data, x),
                                  self._error)
        if flavor == CALLABLE:
            f = _callable_str(s)
            try:
                if s(data):
                    return data
            except SchemaError as x:
                raise SchemaError([None] + x.autos, [e] + x.errors)
            except BaseException as x:
                raise SchemaError('%s(%r) raised %r' % (f, data, x),
                                  self._error)
            raise SchemaError('%s(%r) should evaluate to True' % (f, data), e)
        if s == data:
            return data
        else:
            raise SchemaError('%r does not match %r' % (s, data), e)


class Optional(Schema):

    """Marker for an optional part of Schema."""

    _MARKER = object()

    def __init__(self, *args, **kwargs):
        default = kwargs.pop('default', self._MARKER)
        super(Optional, self).__init__(*args, **kwargs)
        if default is not self._MARKER:
            # See if I can come up with a static key to use for myself:
            if _priority(self._schema) != COMPARABLE:
                raise TypeError(
                    'Optional keys with defaults must have simple, '
                    'predictable values, like literal strings or ints. '
                    '"%r" is too complex.' % (self._schema,))
            self.default = default
            self.key = self._schema


def _callable_str(callable_):
    if hasattr(callable_, '__name__'):
        return callable_.__name__
    return str(callable_)
