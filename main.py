import argparse
import asyncio
import csv
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Run one Jira CSV through configured Gemini and fork-backed Playwright MCP")
    parser.add_argument("csv", type=Path)
    parser.add_argument("--jira-key")
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    parser.add_argument("--normalize-only", action="store_true")
    args = parser.parse_args()
    try:
        if args.normalize_only:
            import json
            from src.jira.parser import parse_csv
            from src.security.secrets import Secrets
            from src.config import Config
            case = parse_csv(args.csv, args.jira_key)
            print(json.dumps(Secrets([*Config.from_env().secret_names, "GEMINI_API_KEY"]).object(case.to_dict()), ensure_ascii=False, indent=2))
            return 0
        from src.runner.test_case_runner import run_case
        report, folder = asyncio.run(run_case(args.csv, args.output_dir, args.jira_key))
        print(f"{report['status']}: {folder.resolve() / 'execution.json'}")
        return {"SUCCESS": 0, "FAILED": 1, "BLOCKED": 2}[report["status"]]
    except FileExistsError:
        print("Output directory already exists. Choose a new --output-dir to preserve prior evidence.")
        return 2
    except (ValueError, OSError, csv.Error) as exc:
        # Do not print arbitrary input or exception data that might contain secrets.
        print(f"Input/configuration error ({type(exc).__name__}). Check CSV, Jira key, paths and environment.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
