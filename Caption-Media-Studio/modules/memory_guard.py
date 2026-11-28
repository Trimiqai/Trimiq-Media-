"""Best-effort memory checks for constrained mobile devices."""

import re

MODEL_MEMORY_MB = {
    "tiny": 700,
    "base": 1000,
    "small": 1800,
    "medium": 3200,
}
def memory_status() -> dict:
    values = {}
    try:
        with open("/proc/meminfo", encoding="ascii") as memory_file:
            for line in memory_file:
                match = re.match(r"^(MemTotal|MemAvailable):\s+(\d+)\s+kB", line)
                if match:
                    values[match.group(1)] = int(match.group(2)) // 1024
    except OSError:
        return {"available_mb": None, "total_mb": None, "source": "unavailable"}
    return {
        "available_mb": values.get("MemAvailable"),
        "total_mb": values.get("MemTotal"),
        "source": "/proc/meminfo" if values else "unavailable",
    }


def ensure_model_memory(model_name: str) -> dict:
    estimate = MODEL_MEMORY_MB.get(model_name)
    if estimate is None:
        raise ValueError("Unsupported Whisper model size.")
    status = memory_status()
    available = status["available_mb"]
    warning = (
        f"The {model_name} model may need more than the {available} MB currently available."
        if available is not None and available < estimate
        else None
    )
    return {**status, "estimated_model_mb": estimate, "warning": warning}
