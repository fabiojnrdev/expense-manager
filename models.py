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
 
# Objetos de valor(imutáveis)
class Money:
    """Valor monetário com reconhecimento de moeda.
    Armazena internamente o valor como Decimal para evitar erros de arredondamento de ponto flutuante
    que são comuns em cálculos financeiros.
    Exemplos
    --------
    >>> m = Money(Decimal("19.99"), "BRL")
    >>> str(m)
    'R$ 19,99'
    """
    amount: Decimal
    currency: str = "BRL"
    
    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal):
            object.__setattr__(self, "amount", Decimal(str(self.amount)))
        if self.amount < 0:
            raise ValueError(f"O valor monetário não pode ser negativo.: {self.amount}")
    def __add__(self, other):
        if self.currency != other.currency:
            raise ValueError(
                f"Não é possível adicionar {self.currency} e {other.currency}"
            )
        return Money(self.amount + other.amount, self.currency)
    def __str__(self) -> str:
        symbols = {"BRL": "R$", "USD": "US$", "EUR": "€"}
        symbol = symbols.get(self.currency, self.currency)
        return f"{symbol} {self.amount:2f}"
    
    @classmethod
    def from_string(cls, raw: str, currency: str = "BRL") -> "Money":
        """Converter uma string bruta como '1.299,50' ou '1299.50' em dinheiro."""
        cleaned = raw.strip().replace("R$", "").replace("US$", "").strip()
        # Lidar com o formato brasileiro: 1.299,50 → 1299,50
        if re.match(r"^\d{1,3}(\.\d{3})*(,\d+)?$", cleaned):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
        try:
            return cls(Decimal(cleaned), currency)
        except InvalidOperation:
            raise ValueError(f"Não foi possível interpretar '{raw}' como um valor monetário.")
@dataclass(frozen=True)
class Category:
    """Categoria de despesa com categoria pai opcional para agrupamento hierárquico.
    Atributos
    ----------
    nome : str
    Nome legível, normalizado para maiúsculas no início de cada palavra.
    pai : Opcional[str]
    Categoria pai para hierarquias de dois níveis (ex.: "Alimentação/Mercado").
    """
    name: str
    parent: Optional[str] = None
    
    def __post_init__(self) -> None:
        normalised = self.name.strip().title()
        object.__setattr__(self,"name", normalised)
        
    @property
