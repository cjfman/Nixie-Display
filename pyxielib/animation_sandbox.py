"""
Handlers for the ``sandbox`` block of the animation DSL.

A sandbox block lets a ``.ani`` file assemble animations from the helper
functions in ``animation_library.py``. Each line in the block is one of:

- assignment: ``name = expression``
- print:      ``print name``
- set:        ``set setting = literal``

Expressions are never executed as Python. They are tokenized and evaluated
by hand (see ``SandboxParser._evaluate``) so that only the explicitly
supported functions, literals, variables, lists and operators are reachable.
"""

import inspect
import re

from typing import Any, Dict, List, Tuple

from pyxielib import animation as animation_module
from pyxielib import animation_library
from pyxielib.animation import (
    Animation, Frame, FullFrame, FullFrameAnimation,
    TimeFullFrame, TubeAnimation, TubeSequence,
    concatFullFrameRows, textToFrames,
)
from pyxielib.animation_file import FileAnimationError


class SandboxError(FileAnimationError):
    """Raised for errors found while parsing a sandbox block"""


## Reserved names that may not be used as sandbox variables: animation file
## keywords/subcommands plus the sandbox statement keywords.
_FILE_KEYWORDS = {
    'sprite', 'segment', 'frame', 'scale', 'sequence', 'repeat', 'flatten',
    'import', 'sandbox', 'end',   'start', 'insert',   'set',    'print',
}

_TYPE_KEYWORDS = {
    'string', 'int', 'list', 'tuple', 'str', 'true', 'false', 'none',
}

## Every class defined in animation.py is reserved as well
_ANIMATION_CLASS_NAMES = {
    name for name, cls in inspect.getmembers(animation_module, inspect.isclass)
    if cls.__module__ == animation_module.__name__
}

_RESERVED_NAMES = { x.lower() for x in (_FILE_KEYWORDS | _ANIMATION_CLASS_NAMES | _TYPE_KEYWORDS) }

## Values assigned to variables must be instances of one of these classes
_ANIMATION_CLASSES: Tuple[type, ...] = tuple(
    cls for _, cls in inspect.getmembers(animation_module, inspect.isclass)
    if cls.__module__ == animation_module.__name__
)

_VAR_NAME_RE = re.compile(r'[A-Za-z]\w+$')
_KEYWORD_RE  = re.compile(r'^(set|print)\b')

## Order matters: floats before ints, longer punctuation handled individually
_TOKEN_RE = re.compile('|'.join([
    r'(?P<string>"[^"]*"|\'[^\']*\')',
    r'(?P<number>-?\d+\.\d+|-?\d+)',
    r'(?P<bool>True|False)',
    r'(?P<none>None)',
    r'(?P<name>[A-Za-z]\w*)',
    r'(?P<op>[+*|])',
    r'(?P<lparen>\()',
    r'(?P<rparen>\))',
    r'(?P<lbracket>\[)',
    r'(?P<rbracket>\])',
    r'(?P<comma>,)',
    r'(?P<assign>=)',
    r'(?P<ws>\s+)',
]))

_SETTINGS = ('delay', 'rate')


class SandboxParser:
    """Parses and evaluates the lines of a single sandbox block"""

    def __init__(self, scale=1.0, size=16):
        self.size = size
        self.variables: Dict[str, Any] = {}
        self.settings:  Dict[str, float] = {'delay': float(scale), 'rate': 0.0}
        self.printed:   List[Animation] = []
        self._last_assigned = None

    ## ----- line dispatch -------------------------------------------------

    def parseLine(self, line):
        """Parse a single line of the sandbox block"""
        line = line.strip()
        keyword = _KEYWORD_RE.match(line)
        if keyword:
            kw = keyword.group(1)
            arg = line[len(kw):].strip()
            if kw == 'set':
                self._parseSet(arg)
            else:
                self._parsePrint(arg)
        elif '=' in line:
            self._parseAssignment(line)
        else:
            raise SandboxError(f"Line is not an assignment, 'set', or 'print': '{line}'")

    ## ----- set -----------------------------------------------------------

    def _parseSet(self, arg):
        if '=' not in arg:
            raise SandboxError(f"'set' requires 'name = value', found 'set {arg}'")

        name, _, value_str = arg.partition('=')
        name = name.strip()
        if name not in _SETTINGS:
            raise SandboxError(f"Unknown setting '{name}'. Valid settings: {', '.join(_SETTINGS)}")

        value = self._parseLiteral(value_str.strip())
        if value is None or isinstance(value, str):
            raise SandboxError(f"Setting '{name}' must be a number")

        value = float(value)
        if value < 0:
            raise SandboxError(f"Setting '{name}' may not be negative")

        self.settings[name] = value
        if value:
            ## delay and rate may not both be non-zero; zero the other
            other = 'rate' if name == 'delay' else 'delay'
            self.settings[other] = 0.0

    ## ----- assignment ----------------------------------------------------

    def _parseAssignment(self, line):
        name, _, expr_str = line.partition('=')
        name = name.strip()
        self._validateVarName(name)

        tokens = self._tokenize(expr_str.strip())
        value = self._evaluate(tokens)
        self._validateValue(name, value)
        self.variables[name] = value
        self._last_assigned = name

    def _validateVarName(self, name):
        if not _VAR_NAME_RE.match(name):
            raise SandboxError(f"Invalid variable name '{name}'")
        if name.lower() in _RESERVED_NAMES:
            raise SandboxError(f"'{name}' is a reserved name and may not be a variable")

    def _validateValue(self, name, value):
        if isinstance(value, _ANIMATION_CLASSES):
            return
        if isinstance(value, list):
            self._validateList(name, value)
            return
        raise SandboxError(
            f"Variable '{name}' must be an animation object, not {type(value).__name__}"
        )

    @staticmethod
    def _validateList(name, value):
        if not value:
            raise SandboxError(f"Variable '{name}' may not be an empty list")
        if not all(isinstance(v, _ANIMATION_CLASSES) for v in value):
            raise SandboxError(f"Every element of variable '{name}' must be an animation object")
        first_type = type(value[0])
        if any(type(v) is not first_type for v in value):
            raise SandboxError(f"Every element of variable '{name}' must be the same type")

    ## ----- print ---------------------------------------------------------

    def _parsePrint(self, arg):
        arg = arg.strip()
        if not arg:
            ## No argument: print the most recently assigned variable
            value = self._lastAssignedValue()
        else:
            value = self._evaluate(self._tokenize(arg))

        self.printed.append(self._toAnimation(value))

    def _lastAssignedValue(self):
        if self._last_assigned is None:
            raise SandboxError("'print' has no argument and no variable has been assigned yet")
        return self.variables[self._last_assigned]

    def _toAnimation(self, value) -> Animation:
        """Convert a printed variable into a FullFrameAnimation or TubeAnimation"""
        if isinstance(value, (FullFrameAnimation, TubeAnimation)):
            return value
        if isinstance(value, Frame):
            return FullFrameAnimation([(self.settings['delay'], FullFrame([value]))])
        if isinstance(value, FullFrame):
            return FullFrameAnimation([(self.settings['delay'], value)])
        if isinstance(value, TubeSequence):
            return TubeAnimation([value])
        if isinstance(value, list):
            return self._listToAnimation(value)
        raise SandboxError(f"Cannot turn {type(value).__name__} into an animation")

    def _listToAnimation(self, value) -> Animation:
        if not value:
            raise SandboxError("Cannot print an empty list")
        first = value[0]
        if any(type(item) is not type(first) for item in value):
            raise SandboxError("Every element of a printed list must be the same type")
        if isinstance(first, FullFrame):
            return self._makeTimedFull(value)
        if isinstance(first, TubeSequence):
            return TubeAnimation(value)
        raise SandboxError("Lists must hold FullFrame or TubeSequence objects to be printed")

    def _makeTimedFull(self, frames) -> FullFrameAnimation:
        delay = self.settings['delay']
        rate = self.settings['rate']
        if not delay and not rate:
            raise SandboxError("Cannot time frames: 'delay' and 'rate' are both zero")
        ## delay overrides rate inside makeTimed; only one is ever non-zero
        return FullFrameAnimation.makeTimed(frames, delay=delay, rate=rate)

    ## ----- expression evaluation -----------------------------------------

    @staticmethod
    def _parseLiteral(text):
        """Parse a string/int/float/None literal for a setting value"""
        if text == 'None':
            return None
        if len(text) >= 2 and text[0] in '"\'' and text[-1] == text[0]:
            return text[1:-1]
        try:
            return int(text)
        except ValueError:
            pass
        try:
            return float(text)
        except ValueError:
            pass
        raise SandboxError(f"Invalid literal '{text}'")

    @staticmethod
    def _tokenize(expr) -> List[Tuple[str, str]]:
        """Break an expression into (kind, text) tokens"""
        tokens: List[Tuple[str, str]] = []
        pos = 0
        while pos < len(expr):
            match = _TOKEN_RE.match(expr, pos)
            if not match:
                raise SandboxError(f"Unexpected character in expression at '{expr[pos:]}'")
            pos = match.end()
            kind = match.lastgroup
            if kind != 'ws':
                tokens.append((kind, match.group()))

        if not tokens:
            raise SandboxError("Empty expression")

        return tokens

    def _evaluate(self, tokens):
        """Second pass: build an expression list of objects/operators, then reduce it"""
        expression: List[Any] = []
        index = 0
        expect_operand = True
        while index < len(tokens):
            kind, text = tokens[index]
            if expect_operand:
                operand, index = self._parseOperand(tokens, index, as_param=False)
                expression.append(operand)
            else:
                if kind != 'op':
                    raise SandboxError(f"Expected '+' or '*' but found '{text}'")
                expression.append(text)
                index += 1
            expect_operand = not expect_operand

        if expect_operand:
            raise SandboxError("Expression ends with an operator")

        return self._reduceExpression(expression)

    def _parseOperand(self, tokens, index, as_param) -> Tuple[Any, int]:
        """Construct a single operand from the token stream"""
        kind, text = tokens[index]
        if kind == 'number':
            return self._numberValue(text), index + 1
        if kind == 'string':
            raw = text[1:-1]
            ## Non-parameter strings become FullFrames; parameters stay raw
            return (raw if as_param else FullFrame(textToFrames(raw))), index + 1
        if kind == 'lbracket':
            return self._parseList(tokens, index)
        if kind == 'name':
            if index + 1 < len(tokens) and tokens[index + 1][0] == 'lparen':
                return self._parseCall(tokens, index)
            return self._lookupVariable(text), index + 1

        raise SandboxError(f"Unexpected token '{text}' in expression")

    @staticmethod
    def _numberValue(text):
        return float(text) if '.' in text else int(text)

    @staticmethod
    def _boolValue(text):
        return (text == 'True')

    def _lookupVariable(self, name):
        if name not in self.variables:
            raise SandboxError(f"Variable '{name}' is not defined")
        return self.variables[name]

    def _parseList(self, tokens, index) -> Tuple[List[Any], int]:
        index += 1  ## skip '['
        items: List[Any] = []
        while True:
            if index >= len(tokens):
                raise SandboxError("Unterminated list")
            if tokens[index][0] == 'rbracket':
                raise SandboxError("Empty lists are not allowed")

            item, index = self._parseOperand(tokens, index, as_param=False)
            items.append(item)

            if index >= len(tokens):
                raise SandboxError("Unterminated list")
            kind, text = tokens[index]
            index += 1
            if kind == 'rbracket':
                break
            if kind != 'comma':
                raise SandboxError(f"Expected ',' or ']' in list but found '{text}'")

        first_type = type(items[0])
        if any(type(item) is not first_type for item in items):
            raise SandboxError("All elements of a list must be the same type")

        return items, index

    def _parseCall(self, tokens, index) -> Tuple[Any, int]:
        name = tokens[index][1]
        func = self._lookupFunction(name)
        index += 2  ## skip name and '('
        args: List[Any] = []
        kwargs: Dict[str, Any] = {}
        while True:
            if index >= len(tokens):
                raise SandboxError(f"Unterminated argument list for '{name}'")
            if tokens[index][0] == 'rparen':
                index += 1
                break

            index = self._parseArg(tokens, index, name, args, kwargs)

            if index >= len(tokens):
                raise SandboxError(f"Unterminated argument list for '{name}'")
            kind, text = tokens[index]
            index += 1
            if kind == 'rparen':
                break
            if kind != 'comma':
                raise SandboxError(f"Expected ',' or ')' in arguments but found '{text}'")

        return self._callFunction(func, name, args, kwargs), index

    def _parseArg(self, tokens, index, func_name, args, kwargs) -> int:
        """Append one positional or keyword argument; returns the next index"""
        kind, text = tokens[index]
        ## Keyword argument: name '=' value
        if kind == 'name' and index + 1 < len(tokens) and tokens[index + 1][0] == 'assign':
            if text in kwargs:
                raise SandboxError(f"Duplicate keyword argument '{text}' to '{func_name}'")
            value, index = self._parseArgValue(tokens, index + 2, func_name)
            kwargs[text] = value
            return index

        if kwargs:
            raise SandboxError(f"Positional argument after keyword argument in '{func_name}'")
        value, index = self._parseArgValue(tokens, index, func_name)
        args.append(value)
        return index

    def _parseArgValue(self, tokens, index, func_name) -> Tuple[Any, int]:
        """Argument values may only be defined variables or literals"""
        kind, text = tokens[index]
        if kind == 'number':
            return self._numberValue(text), index + 1
        if kind == 'string':
            return text[1:-1], index + 1
        if kind == 'bool':
            return self._boolValue(text), index + 1
        if kind == 'none':
            return None, index + 1
        if kind == 'name':
            if index + 1 < len(tokens) and tokens[index + 1][0] == 'lparen':
                raise SandboxError(f"Function calls are not allowed as arguments to '{func_name}'")
            return self._lookupVariable(text), index + 1

        raise SandboxError(f"Arguments to '{func_name}' must be variables or literals, found '{text}'")

    @staticmethod
    def _lookupFunction(name):
        for candidate in (name, 'make' + name):
            func = getattr(animation_library, candidate, None)
            if inspect.isfunction(func) and func.__module__ == animation_library.__name__:
                return func
        raise SandboxError(f"No function '{name}' (or 'make{name}') in animation_library")

    @staticmethod
    def _callFunction(func, name, args, kwargs):
        try:
            return func(*args, **kwargs)
        except SandboxError:
            raise
        except Exception as e:
            raise SandboxError(f"Error calling '{name}': {e}")

    def _reduceExpression(self, expression):
        """Reduce [operand, op, operand, ...] honoring '*' before '+' before '|'"""
        expression = self._reduceOperator(expression, '*')
        expression = self._reduceOperator(expression, '+')
        expression = self._reduceOperator(expression, '|')
        return expression[0]

    def _reduceOperator(self, expression, target):
        """Collapse every occurrence of one operator left to right"""
        reduced: List[Any] = [expression[0]]
        index = 1
        while index < len(expression):
            op = expression[index]
            operand = expression[index + 1]
            if op == target:
                reduced[-1] = self._applyOp(op, reduced[-1], operand)
            else:
                reduced.append(op)
                reduced.append(operand)
            index += 2

        return reduced

    def _applyOp(self, op, left, right):
        if op == '|':
            return self._concatTubes(left, right)
        try:
            return left * right if op == '*' else left + right
        except SandboxError:
            raise
        except Exception as e:
            raise SandboxError(
                f"Cannot apply '{op}' to {type(left).__name__} and {type(right).__name__}: {e}"
            )

    ## ----- tube concatenation ('|') --------------------------------------

    def _concatTubes(self, left, right):
        """Concatenate tubes, delegating to the '|' operators defined in animation.py"""
        ## A bare row list (List[FullFrame]) can't carry an operator, so join it here
        if isinstance(left, list) or isinstance(right, list):
            left_rows = self._asRows(left)
            right_rows = self._asRows(right)
            if left_rows is None or right_rows is None:
                raise SandboxError(
                    f"'|' cannot concatenate {type(left).__name__} with {type(right).__name__}"
                )
            return concatFullFrameRows(left_rows, right_rows)

        try:
            return left | right
        except TypeError:
            raise SandboxError(
                f"'|' cannot concatenate {type(left).__name__} with {type(right).__name__}"
            )

    @staticmethod
    def _asRows(value):
        """A TubeSequence or List[FullFrame] as a list of FullFrame rows, else None"""
        if isinstance(value, TubeSequence):
            return [FullFrame([frame]) for _, frame in value.frames]
        if isinstance(value, list) and value and all(isinstance(v, FullFrame) for v in value):
            return list(value)
        return None

    ## ----- output --------------------------------------------------------

    def fullFrames(self) -> List[TimeFullFrame]:
        """Flatten every printed animation into a list of timed full frames"""
        frames: List[TimeFullFrame] = []
        for animation in self.printed:
            frames.extend(self._animationFrames(animation))

        return frames

    def _animationFrames(self, animation) -> List[TimeFullFrame]:
        if isinstance(animation, TubeAnimation):
            animation = animation.toFullFrameAnimation()
        if isinstance(animation, FullFrameAnimation):
            return list(animation.frames)
        raise SandboxError(f"Cannot render a {type(animation).__name__}")
