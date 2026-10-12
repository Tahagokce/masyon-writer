import os


def process_environment(secret_names: list[str]) -> dict[str, str]:
    # Never forward Gemini's API key, shell debugging or arbitrary environment secrets.
    names = {"PATH", "HOME", "TMPDIR", "SYSTEMROOT", "LANG", "NODE_EXTRA_CA_CERTS",
             "PLAYWRIGHT_FORK_PATH", "PLAYWRIGHT_EXECUTABLE_PATH", "PLAYWRIGHT_BROWSERS_PATH",
             "HEADLESS", "SECRET_ENV_NAMES", "LOG_LEVEL", "LOG_LOCATORS", *secret_names}
    env = {name: os.environ[name] for name in names if name in os.environ}
    env["SECRET_ENV_NAMES"] = ",".join(secret_names)
    return env
