"""Generate a conservative Java+Cucumber handoff from a Jira case."""

import argparse
import os
from pathlib import Path

from src.automation.generator import generate
from src.automation.input_normalizer import normalize
from src.automation.masyon_adapter import MasyonInputAdapter
from src.jira.parser import parse_csv


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path, nargs="?", help="Legacy Jira CSV input")
    parser.add_argument("--jira-case", type=Path, help="Normalized Jira case JSON")
    parser.add_argument("--input", type=Path, help="MASYON JSON or manual Playwright file")
    parser.add_argument("--input-type", choices=["masyon", "playwright", "canonical"])
    parser.add_argument("--test-case-id", help="ID for manual Playwright input")
    parser.add_argument("--jira-key")
    parser.add_argument("--automation-repo", type=Path,
                        default=Path(os.environ["AUTOMATION_REPO_PATH"]) if os.environ.get("AUTOMATION_REPO_PATH") else None)
    parser.add_argument("--recording", type=Path, action="append", default=[])
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    parser.add_argument("--no-validate", action="store_true", help="Skip Maven validation")
    args = parser.parse_args()
    if sum(bool(x) for x in (args.csv, args.jira_case, args.input)) != 1:
        parser.error("Supply exactly one of CSV, --jira-case, or --input")
    if args.automation_repo and not args.automation_repo.is_absolute():
        parser.error("AUTOMATION_REPO_PATH/--automation-repo must be an absolute path")
    if args.csv:
        model = MasyonInputAdapter().from_test_case(parse_csv(args.csv, args.jira_key))
    else:
        model = normalize(args.input or args.jira_case, args.input_type, args.test_case_id)
    folder = args.output_dir / model.testCaseId / "automation" if args.csv else args.output_dir
    folder = generate(model, folder, args.automation_repo, args.recording, not args.no_validate)
    print(folder.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
