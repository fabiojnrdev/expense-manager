from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum, auto
from typing import Optional

# Enumerações

class InputSource(Enum):
    """Origem dos dados"""
    
    TERMINAL = auto()
    CSV_FILE = auto()
    STDIN_PIPE = auto()
    
class ReportFormat(Enum):
    """Formatos suportados para exportação"""
    PDF = "pdf"
    CSV = "csv"
    JSON = "json"
 