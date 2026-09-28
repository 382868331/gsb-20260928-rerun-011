"""EDL: a small teaching language used by the visual debugger.

The package contains a lexer, parser, an event-generating interpreter and a
debug session controller.  Everything is implemented with the Python standard
library only.
"""

from .interpreter import Interpreter, RuntimeEduError
from .parser import ParseEduError, parse_program

__all__ = ["Interpreter", "RuntimeEduError", "ParseEduError", "parse_program"]
