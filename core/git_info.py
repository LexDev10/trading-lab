"""git_sha compartido — se persiste en cada `decision_logs` y lo expone
`/health` (sección 17)."""

import subprocess


def get_git_sha() -> str:
    try:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL
            )
            .decode()
            .strip()
        )
    except Exception:
        return "unknown"
