"""The names that the vendored bs4 imports from typing_extensions. The
typing module has all of them (Self from Python 3.11), so this file takes
the place of the typing_extensions package."""
from typing import Any, Literal, Protocol, TypeAlias, runtime_checkable

try:
    from typing import Self
except ImportError:  # Python 3.10. bs4 uses Self only in type hints.
    Self = Any

__all__ = ['Literal', 'Protocol', 'Self', 'TypeAlias', 'runtime_checkable']
