import inspect


class PyxieError(Exception):
    def __init__(self, msg, *args, **kwargs):
        Exception.__init__(self, msg, *args, **kwargs)
        self.msg = msg

    def what(self):
        return self.msg

    def __str__(self):
        return f"PyxieLib error: {self.msg}"

    def __repr__(self):
        return f"<{self.__class__.__name__} '{self.msg}'>"


class PyxieUnimplementedError(PyxieError):
    def __init__(self, obj, *args, **kwargs):
        fname = inspect.stack()[1][3]
        cname = type(obj).__name__
        PyxieError.__init__(self, f"{cname}.{fname}() is unimplemented")
