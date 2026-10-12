"""Validate a proposal in a temporary repository copy, leaving source untouched."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path


def validate(index: dict, repository: Path | None, proposal_root: Path,
             feature_path: Path | None = None, run_maven: bool = True) -> dict:
    expressions: dict[str, list[str]] = {}
    for binding in index["bindings"]:
        expressions.setdefault(binding["expression"], []).append(binding["source"])
    result = {"duplicateDefinitions": {k: v for k, v in expressions.items() if len(v) > 1},
              "mavenCompile": "UNAVAILABLE", "mavenTest": "UNAVAILABLE",
              "cucumberDryRun": "UNAVAILABLE"}
    if repository is None or not (repository / "pom.xml").is_file():
        return result
    if not run_maven:
        result.update({"mavenCompile": "NOT_RUN", "mavenTest": "NOT_RUN", "cucumberDryRun": "NOT_RUN"})
        return result
    if not shutil.which("mvn"):
        result["mavenCompile"] = "MAVEN_NOT_FOUND"
        return result
    with tempfile.TemporaryDirectory(prefix="masyon-automation-") as directory:
        shadow = Path(directory) / "repository"
        shutil.copytree(repository, shadow, ignore=shutil.ignore_patterns(".git", "target", "build", ".gradle", "node_modules"))
        for path in proposal_root.rglob("*.java") if proposal_root.exists() else []:
            destination = shadow / path.relative_to(proposal_root)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
        if feature_path:
            destination = shadow / "src/test/resources/features" / feature_path.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(feature_path, destination)
        mvn = "mvn"
        pom_text = (shadow / "pom.xml").read_text(encoding="utf-8", errors="replace")
        has_cucumber_runner = ("cucumber-junit-platform-engine" in pom_text or
                               any("@RunWith(Cucumber.class)" in p.read_text(encoding="utf-8", errors="replace")
                                   for p in shadow.rglob("*.java")))
        commands = [("mavenCompile", [mvn, "-q", "-DskipTests", "test-compile"]),
                    ("mavenTest", [mvn, "-q", "test", "-Dcucumber.execution.dry-run=true"])]
        for name, command in commands:
            try:
                done = subprocess.run(command, cwd=shadow, text=True, stdout=subprocess.PIPE,
                                      stderr=subprocess.STDOUT, timeout=240, check=False)
                result[name] = {"exitCode": done.returncode, "outputTail": done.stdout[-4000:]}
                if name == "mavenTest":
                    result["cucumberDryRun"] = (("PASS" if done.returncode == 0 else "FAILED")
                                                 if has_cucumber_runner else "UNSUPPORTED_NO_RUNNER")
                if name == "mavenCompile" and done.returncode:
                    break
            except (OSError, subprocess.TimeoutExpired) as exc:
                result[name] = {"error": type(exc).__name__}
                break
    return result
