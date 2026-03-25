"""
core/exceptions.py
==================
Hierarquia de exceções específicas do domínio para o sistema Expense Tracker.

Todas as exceções da aplicação herdam de ``ExpenseTrackerError`` para que quem chama
possa capturar toda a hierarquia com um único ``except``, mantendo a possibilidade
de diferenciar tipos específicos quando necessário.
"""


class ExpenseTrackerError(Exception):
    """Exceção base para todos os erros da aplicação."""


class ParseError(ExpenseTrackerError):
    """Lançada quando os dados de entrada não podem ser convertidos em uma Expense válida."""

    def __init__(self, message: str, raw_line: str = "", line_number: int = 0):
        self.raw_line = raw_line
        self.line_number = line_number
        super().__init__(
            f"Parse error at line {line_number}: {message} | raw='{raw_line}'"
        )


class ValidationError(ExpenseTrackerError):
    """Lançada quando um registro já parseado falha na validação das regras de negócio."""

    def __init__(self, message: str, field_name: str = ""):
        self.field_name = field_name
        super().__init__(
            f"Validation failed on field '{field_name}': {message}"
        )


class AggregationError(ExpenseTrackerError):
    """Lançada quando o agente de agregação encontra um estado inconsistente."""


class RenderError(ExpenseTrackerError):
    """Lançada quando o agente de renderização de PDF/gráficos falha."""


class ConfigurationError(ExpenseTrackerError):
    """Lançada quando a aplicação está configurada incorretamente."""