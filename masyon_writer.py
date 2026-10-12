"""MASYON-WRITER command line: normalize inputs and generate one proposal."""

import argparse
import os
from pathlib import Path

from src.automation.canonical import UnsupportedInputError
from src.automation.generator import generate
from src.automation.input_normalizer import export_canonical, normalize


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="masyon-writer")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("normalize", "generate"):
        command = commands.add_parser(name)
        command.add_argument("--input", type=Path, required=True)
        command.add_argument("--input-type", choices=["masyon", "playwright", "canonical"])
        command.add_argument("--test-case-id")
        command.add_argument("--output-dir", type=Path)
        if name == "generate":
            command.add_argument("--automation-repo", type=Path,
                                 default=Path(os.environ["AUTOMATION_REPO_PATH"])
                                 if os.environ.get("AUTOMATION_REPO_PATH") else None)
            command.add_argument("--recording", type=Path, action="append", default=[])
            command.add_argument("--no-validate", action="store_true")
    args = parser.parse_args(argv)
    try:
        model = normalize(args.input, args.input_type, args.test_case_id)
        output = args.output_dir or Path("outputs") / model.testCaseId / (
            "normalization" if args.command == "normalize" else "automation")
        if args.command == "normalize":
            destination = export_canonical(model, output)
        else:
            if args.automation_repo and not args.automation_repo.is_absolute():
                parser.error("AUTOMATION_REPO_PATH/--automation-repo must be absolute")
            destination = generate(model, output, args.automation_repo, args.recording,
                                   not args.no_validate)
        print(destination.resolve())
        return 0
    except UnsupportedInputError as exc:
        print(f"{exc.code}: {exc}")
        return 2
    except FileExistsError as exc:
        print(f"Output already exists: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
