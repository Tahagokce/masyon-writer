import tempfile
import unittest
import shutil
import subprocess
from unittest.mock import patch
from pathlib import Path

from src.automation.generator import GherkinStep, feature_steps, generate, match_binding, scan_bindings
from src.jira.models import Step, TestCase
from src.automation.repository import index_repository
from src.automation.validation import validate
from src.automation.masyon_adapter import MasyonInputAdapter
import json


class AutomationGenerationTests(unittest.TestCase):
    def test_order_data_and_expected_result_are_preserved(self):
        case = TestCase("WBT-1", [Step(1, "SL Type seçilir.", "SL_TYPE=Deadload",
                                       "Deadload seçildiği görülür.")])
        steps = feature_steps(MasyonInputAdapter().from_test_case(case))
        self.assertEqual([s.kind for s in steps], ["action", "expectedResult"])
        self.assertIn("SL_TYPE=Deadload", steps[0].text)
        self.assertEqual(steps[1].text, "Deadload seçildiği görülür")

    def test_exact_parameterized_and_semantic_reuse(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            java = repo / "src/test/java/Steps.java"
            java.parent.mkdir(parents=True)
            java.write_text('''import io.cucumber.java.en.*;
public class Steps {
  @Given("Uygulamaya giriş yapılır")
  public void login() {}
  @When("{string} sekmesine tıklanır")
  public void tab(String name) {}
}''', encoding="utf-8")
            bindings = scan_bindings(repo)
        exact = GherkinStep(1, "action", "Given", "Uygulamaya giriş yapılır")
        parameterized = GherkinStep(2, "action", "When", '"Passenger" sekmesine tıklanır')
        semantic = GherkinStep(3, "action", "When", "Alt sekmelerden SL Codes sekmesine geçilir")
        self.assertEqual(match_binding(exact, bindings)[1], "EXACT_REUSE")
        self.assertEqual(match_binding(parameterized, bindings)[1], "PARAMETERIZED_REUSE")
        matched, kind, text = match_binding(semantic, bindings)
        self.assertEqual(kind, "SEMANTIC_REUSE")
        self.assertEqual(text, '"SL Codes" sekmesine tıklanır')
        self.assertEqual(matched.expression, "{string} sekmesine tıklanır")

    def test_unknown_behavior_is_not_called_reuse(self):
        step = GherkinStep(4, "action", "When", "Create New Definition butonuna tıklanır")
        self.assertEqual(match_binding(step, [])[1], "MISSING")

    def test_regex_and_comments_do_not_create_false_reuse(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "WeightSteps.java").write_text(r'''import io.cucumber.java.en.And;
public class WeightSteps {
    // @When("weight 42 girilir")
    @And("^weight (\\d+) girilir$")
    public void weight(int value) {}
}''', encoding="utf-8")
            bindings = scan_bindings(repo)
            self.assertEqual(len(bindings), 1)
            self.assertEqual(match_binding(GherkinStep(1, "action", "When", "weight 42 girilir"),
                                           bindings)[1], "PARAMETERIZED_REUSE")

    def test_locator_index_rejects_dynamic_id_and_keeps_test_id(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "SamplePage.java").write_text('''public class SamplePage {
  private By dynamicButton = By.id("ember-123456");
  private By saveButton = By.cssSelector("[data-testid='save-button']");
}''', encoding="utf-8")
            index = index_repository(repo)
            self.assertEqual([(x["kind"], x["value"], x["stable"]) for x in index["locators"]],
                             [("id", "ember-123456", False), ("testid", "save-button", True)])

    def test_proposal_directory_cannot_be_inside_external_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            case = TestCase("WBT-2", [Step(1, "Uygulamaya giriş yapılır")])
            with self.assertRaises(ValueError):
                generate(MasyonInputAdapter().from_test_case(case), repo / "proposal", repo, run_validation=False)
            self.assertFalse((repo / "proposal").exists())

    def test_maven_validation_uses_temporary_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "external"
            repo.mkdir()
            (repo / "pom.xml").write_text("<project>cucumber-junit-platform-engine</project>")
            feature = root / "WBT-3.feature"
            feature.write_text("Feature: WBT-3\n  Scenario: x\n    Given x\n")
            calls = []
            def fake_run(command, **kwargs):
                calls.append((command, Path(kwargs["cwd"])))
                self.assertNotEqual(Path(kwargs["cwd"]), repo)
                self.assertTrue((Path(kwargs["cwd"]) / "src/test/resources/features/WBT-3.feature").exists())
                return subprocess.CompletedProcess(command, 0, stdout="")
            with patch("src.automation.validation.shutil.which", return_value="/usr/bin/mvn"), \
                 patch("src.automation.validation.subprocess.run", side_effect=fake_run):
                result = validate(index_repository(repo), repo, root / "proposed", feature)
            self.assertEqual(len(calls), 2)
            self.assertEqual(result["mavenCompile"]["exitCode"], 0)
            self.assertEqual(result["cucumberDryRun"], "PASS")
            self.assertFalse((repo / "src").exists())

    def test_regex_ambiguity_and_external_patch_is_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "external-repo"
            java_dir = repo / "src/test/java/com/example/steps"
            page_dir = repo / "src/test/java/com/example/pages"
            java_dir.mkdir(parents=True)
            page_dir.mkdir(parents=True)
            step_file = java_dir / "LoginSteps.java"
            step_file.write_text('''package com.example.steps;
import com.example.pages.SavePage;
import io.cucumber.java.en.Given;
public class LoginSteps {
    private final SavePage savePage;
    public LoginSteps(SavePage savePage) { this.savePage = savePage; }
    @Given("Uygulamaya giriş yapılır")
    public void login() {}
}''', encoding="utf-8")
            page_file = page_dir / "SavePage.java"
            page_file.write_text('''package com.example.pages;
import org.openqa.selenium.By;
public class SavePage {
    private By saveButton = By.id("save-button");
    public void clickSaveButton() { saveButton.toString(); }
}''', encoding="utf-8")
            original = step_file.read_bytes()
            index = index_repository(repo)
            self.assertEqual(len(index["pageObjects"]), 1)
            self.assertEqual(index["locators"][0]["value"], "save-button")
            case = TestCase("WBT-9", [Step(1, "Uygulamaya giriş yapılır"),
                                       Step(2, "Save butonuna tıklanır")])
            output = root / "proposal"
            generate(MasyonInputAdapter().from_test_case(case), output, repo, run_validation=False)
            reuse = json.loads((output / "reuse-report.json").read_text())
            self.assertEqual([item["status"] for item in reuse], ["EXACT_REUSE", "MISSING"])
            self.assertEqual(reuse[1]["javaProposal"]["status"], "PROPOSED")
            self.assertEqual(step_file.read_bytes(), original)
            proposed = output / "proposed-patch/src/test/java/com/example/steps/LoginSteps.java"
            self.assertIn("savePage.clickSaveButton();", proposed.read_text())
            if shutil.which("javac"):
                stubs = root / "stubs"
                for relative, source in {
                    "org/openqa/selenium/By.java": '''package org.openqa.selenium;
public class By { public static By id(String value) { return new By(); } }''',
                    "io/cucumber/java/en/Given.java": '''package io.cucumber.java.en;
public @interface Given { String value(); }''',
                    "io/cucumber/java/en/When.java": '''package io.cucumber.java.en;
public @interface When { String value(); }''',
                }.items():
                    path = stubs / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(source, encoding="utf-8")
                sources = [str(p) for p in stubs.rglob("*.java")]
                sources += [str(page_file), str(proposed)]
                result = subprocess.run(["javac", "-d", str(root / "classes"), *sources],
                                        capture_output=True, text=True, check=False)
                self.assertEqual(result.returncode, 0, result.stderr)

            (java_dir / "RegexSteps.java").write_text('''package com.example.steps;
import io.cucumber.java.en.When;
import io.cucumber.java.en.And;
public class RegexSteps {
    @When("^Save butonuna tıklanır$")
    public void regex() {}
    @And("Save butonuna tıklanır")
    public void literal() {}
}''', encoding="utf-8")
            from src.automation.repository import Binding
            bindings = [Binding(**entry) for entry in index_repository(repo)["bindings"]]
            self.assertEqual(match_binding(GherkinStep(2, "action", "When", "Save butonuna tıklanır"),
                                           bindings)[1], "AMBIGUOUS")


if __name__ == "__main__":
    unittest.main()
