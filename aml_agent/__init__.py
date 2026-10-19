"""AML alert triage agent: an agentic investigator for bank transaction monitoring alerts."""

__version__ = "1.0.0"
__all__ = ["DataStore", "TriageAgent", "CaseResult"]


def __getattr__(name):  # lazy imports keep `import aml_agent` light
    if name == "DataStore":
        from .data_store import DataStore
        return DataStore
    if name in {"TriageAgent", "CaseResult"}:
        from . import agent
        return getattr(agent, name)
    raise AttributeError(name)
