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
@dataclass(frozen=True)
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
                f"Cannot add {self.currency} and {other.currency}"
            )
        return Money(self.amount + other.amount, self.currency)
    def __str__(self) -> str:
        symbols = {"BRL": "R$", "USD": "US$", "EUR": "€"}
        symbol = symbols.get(self.currency, self.currency)
        return f"{symbol} {self.amount:,.2f}"
    
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
            raise ValueError(f"Cannot parse '{raw}' as a monetary amount.")
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
    def full_name(self) -> str:
        return f"{self.parent}/{self.name}" if self.parent else self.name
    def __str__(self) -> str:
        return self.full_name
    
# Entidade principal
@dataclass(frozen=True)
class Expense:
    """
    Entidade principal do domínio que representa uma única despesa financeira.
    Atributos
    ---------
    descrição: str
    Descrição da despesa em texto livre.
    valor: Money
    Valor monetário.
    categoria: Categor
    Classificação da despesa.
    data_da_despesa: dat
    Data em que a despesa ocorreu (o padrão é hoje).
    tags: list[str]
    Tags opcionais de formato livre para pesquisa entre categorias.
    origem: InputSourc
    Origem deste registro.
    """
    description: str
    amount: Money
    category: Category
    expense_date: date = field(default_factory=date.today)
    tags: list[str] = field(default_factory=list)
    source: InputSource = InputSource.TERMINAL
    
    def __post_init__(self) -> None:
        if not self.description.strip():
            raise ValueError("A descrição da despesa não pode estar vazia.")
    def to_dict(self) -> dict:
        """Serializa para um dicionário simples (compatível com JSON)."""
        return {
             "description": self.description,
            "amount": float(self.amount.amount),
            "currency": self.amount.currency,
            "category": self.category.name,
            "parent_category": self.category.parent,
            "date": self.expense_date.isoformat(),
            "tags": self.tags,
            "source": self.source.name,
        }
# Modelos de resultados agregados

@dataclass(frozen=True)
class CategorySummary:
    """
    Estatísticas agregadas para uma categoria.
    Atributos
    ---------
    nome_da_categoria: str
    total: Mone
    contagem: int
    Número de despesas individuais nesta categoria.
    percentual: float
    Fração do total geral (0-100).
    média: Mone
    Valor médio da despesa para esta categoria.
    """
    category_name: str
    total: Money
    count: int
    percentage: float
    average: Money
 
    def __str__(self) -> str:
        return (
            f"{self.category_name}: {self.total} "
            f"({self.percentage:.1f}%, {self.count} items)"
        )
 
@dataclass
class ExpenseReport:
    """
    Relatório completo processado pronto para renderização.
 
    Atributos
    ----------
    summaries : list[CategorySummary]
        Agregações por categoria, ordenadas pelo total em ordem decrescente.
    grand_total : Money
    expenses : list[Expense]
        Todas as despesas individuais incluídas neste relatório.
    generated_at : datetime
    period_start : Optional[date]
    period_end : Optional[date]
    metrics : ReportMetrics
    """
    summaries: list[CategorySummary]
    grand_total: Money
    expenses: list[Expense]
    generated_at: datetime = field(default_factory=datetime.now)
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    metrics: Optional["ReportMetrics"] = None
 
 
@dataclass(frozen=True)
class ReportMetrics:
    """
    Métricas operacionais e analíticas capturadas durante a geração do relatório.
 
    Elas são usadas para observabilidade, garantia de qualidade e feedback ao usuário.
    Todos os valores de tempo estão em segundos.
    """
    # Qualidade dos dados de entrada
    total_records_read: int
    valid_records: int
    invalid_records: int
    duplicate_records: int
 
    # Processamento
    parse_duration_s: float
    aggregation_duration_s: float
    render_duration_s: float
 
    # Insights dos dados
    category_count: int
    date_range_days: int
    largest_single_expense: float
    smallest_single_expense: float
    std_deviation: float
 
    @property
    def total_duration_s(self) -> float:
        return self.parse_duration_s + self.aggregation_duration_s + self.render_duration_s
 
    @property
    def data_quality_pct(self) -> float:
        if self.total_records_read == 0:
            return 0.0
        return (self.valid_records / self.total_records_read) * 100
 
    def summary_lines(self) -> list[str]:
        """Retorna linhas de métricas legíveis para exibição em CLI."""
        return [
            f"  Registros lidos   : {self.total_records_read}",
            f"  Válidos / Inválidos: {self.valid_records} / {self.invalid_records}",
            f"  Duplicados ignorados: {self.duplicate_records}",
            f"  Qualidade dos dados: {self.data_quality_pct:.1f}%",
            f"  Categorias encontradas: {self.category_count}",
            f"  Intervalo de datas: {self.date_range_days} dias",
            f"  Maior despesa     : R$ {self.largest_single_expense:,.2f}",
            f"  Menor despesa     : R$ {self.smallest_single_expense:,.2f}",
            f"  Desvio padrão     : R$ {self.std_deviation:,.2f}",
            f"  Tempo de parsing  : {self.parse_duration_s:.3f}s",
            f"  Tempo de agregação: {self.aggregation_duration_s:.3f}s",
            f"  Tempo de renderização: {self.render_duration_s:.3f}s",
            f"  Tempo total       : {self.total_duration_s:.3f}s",
        ]