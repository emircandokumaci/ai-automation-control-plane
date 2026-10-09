from .http import HTTPAdapter
from .n8n import N8NAdapter
from .python import PythonAdapter

ADAPTERS = {
    "http": HTTPAdapter(),
    "n8n": N8NAdapter(),
    "python": PythonAdapter(),
}


def get_adapter(name: str):
    try:
        return ADAPTERS[name]
    except KeyError:
        raise ValueError(f"unsupported adapter: {name}")
