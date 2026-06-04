"""Разбор свободного текста клиента в структурированный CustomerRequest."""

from .factory import get_parser
from .parser import RequestParser
from .rule_parser import RuleBasedParser

__all__ = ["RequestParser", "RuleBasedParser", "get_parser"]
