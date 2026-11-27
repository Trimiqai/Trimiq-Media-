"""Best-effort memory checks for constrained mobile devices."""

import re

MODEL_MEMORY_MB = {
    "tiny": 700,
    "base": 1000,
    "small": 1800,
    "medium": 3200,
}
RESERVE_MB = 350


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
    if available is not None and available < estimate + RESERVE_MB:
        raise RuntimeError(
            f"Not enough currently available memory for the {model_name} model "
            f"(estimated {estimate} MB plus {RESERVE_MB} MB reserve; "
            f"{available} MB available). Close other apps or select a smaller model."
        )
    return {**status, "estimated_model_mb": estimate, "reserve_mb": RESERVE_MB}
