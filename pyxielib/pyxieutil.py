import inspect

from bs4 import BeautifulSoup


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


def flattenHTML(html):
    """Parse html content and remove the tags"""
    soup = BeautifulSoup(html, "html.parser")
    for data in soup(['style', 'script']):
        ## Remove tags
        data.decompose()

    ## Return data by retrieving the tag content
    return ' '.join(soup.stripped_strings)


def strToInt(num):
    prefix = num[:2]
    if prefix == '0x':
        return int(num, 16)
    elif prefix == '0b':
        return int(num, 2)
    elif prefix[0] == '0':
        return int(num, 8)

    return int(num)
