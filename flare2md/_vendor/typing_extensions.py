"""The names that the vendored bs4 imports from typing_extensions. Python
3.11 has all of them in typing, so this file takes the place of the
typing_extensions package."""
from typing import Literal, Protocol, Self, TypeAlias, runtime_checkable

__all__ = ['Literal', 'Protocol', 'Self', 'TypeAlias', 'runtime_checkable']
