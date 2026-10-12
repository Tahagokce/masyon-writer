import json
import tempfile
import unittest
from pathlib import Path

from src.automation.canonical import LocatorEvidence, UnsupportedInputError
from src.automation.input_normalizer import detect_input_type, export_canonical, normalize
from src.automation.generator import feature_steps
from src.automation.proposals import locator_evidence
from src.automation.masyon_adapter import MasyonInputAdapter
from src.automation.playwright_adapter import PlaywrightInputAdapter


class InputNormalizationTests(unittest.TestCase):
    def test_masyon_handoff_preserves_plan_data_page_and_proven_locator(self):
        payload = {"jiraKey": "WBT-1", "steps": [{"stepNumber": 1,
            "action": "Save butonuna tıklanır", "data": "SL_CODE=TSX",
            "expectedResult": "Dialog açılır", "page": {"alias": "popup"},
            "semanticPlan": {"actions": [{"type": "click", "target": {"name": "Save", "role": "button"}}],
                             "expectedAssertions": [{"type": "visible", "target": {"role": "dialog"}}]},
            "locatorEvidence": [{"strategy": "id", "value": "save-button", "confidence": "PROVEN"}]}]}
        model = MasyonInputAdapter().from_mapping(payload)
        step = model.steps[0]
        self.assertEqual(model.sourceType, "MASYON")
        self.assertEqual(step.actions[0].type, "CLICK")
        self.assertEqual(step.actions[0].target, {"name": "Save", "role": "button"})
        self.assertEqual(step.data["SL_CODE"], "TSX")
        self.assertEqual(step.locatorEvidence[0].confidence, "PROVEN")
        self.assertEqual(step.assertions[0].type, "VISIBLE")
        self.assertEqual(step.page["alias"], "popup")
        self.assertEqual(step.sourceMetadata["original"], payload["steps"][0])

    def test_masyon_execution_reads_companion_and_does_not_promote_failed_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "normalized-test-case.json").write_text(json.dumps({"jiraKey": "WBT-2", "steps": [
                {"stepNumber": 1, "action": "Save butonuna tıklanır", "data": "", "expectedResult": ""}]}))
            (folder / "execution.json").write_text(json.dumps({"jiraKey": "WBT-2", "status": "FAILED",
                "steps": [{"stepNumber": 1, "status": "FAILED", "reason": "DNS"}]}))
            (folder / "recording.spec.ts").write_text("throw new Error('FAILED: DNS');")
            model = normalize(folder / "execution.json")
            self.assertEqual(model.steps[0].sourceMetadata["execution"]["status"], "FAILED")
            self.assertEqual(model.steps[0].locatorEvidence, [])
            self.assertEqual(model.sourceMetadata["recordingArtifact"]["locatorAssignment"],
                             "UNASSIGNED_WITHOUT_STEP_MAPPING")
            self.assertEqual(len(model.sourceMetadata["recordingArtifact"]["sha256"]), 64)

    def test_masyon_wording_infers_tab_click_and_selected_assertion(self):
        model = MasyonInputAdapter().from_mapping({"jiraKey": "WBT-6", "steps": [{
            "stepNumber": 1, "action": "SL Codes sekmesine geçilir",
            "expectedResult": "SL Codes sekmesinin açık olduğu görülür"}]})
        self.assertEqual(model.steps[0].actions[0].type, "CLICK")
        self.assertEqual(model.steps[0].actions[0].target["role"], "tab")
        self.assertEqual(model.steps[0].actions[0].target["name"], "SL Codes")
        self.assertEqual(model.steps[0].assertions[0].type, "SELECTED")
        self.assertEqual(model.steps[0].actions[0].provenance["confidence"], "INFERRED")

    def test_playwright_supported_locators_actions_and_assertions_in_order(self):
        text = '''import { test, expect } from '@playwright/test';
test('WBT-3', async ({ page }) => {
  await page.locator('#special-load-tab').click();
  await page.locator('#sl-code').fill('TSX');
  await page.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.getByLabel('Active').check();
  await expect(page.getByLabel('Active')).toBeChecked();
  await page.getByTestId('load-weight').fill('42');
  await expect(page.getByTestId('load-weight')).toHaveValue('42');
  await page.getByPlaceholder('Code').fill('ABC');
  await page.getByText('Continue').click();
});'''
        model = PlaywrightInputAdapter().from_text(text, test_case_id="WBT-3")
        self.assertEqual([s.actions[0].type for s in model.steps],
                         ["CLICK", "FILL", "CLICK", "CHECK", "FILL", "FILL", "CLICK"])
        self.assertEqual(model.steps[0].locatorEvidence[0].strategy, "id")
        self.assertEqual(model.steps[1].actions[0].value, "TSX")
        self.assertEqual(model.steps[2].target, {"role": "button", "name": "Save"})
        self.assertEqual(model.steps[2].assertions[0].type, "VISIBLE")
        self.assertEqual(model.steps[3].locatorEvidence[0].strategy, "label")
        self.assertEqual(model.steps[3].assertions[0].type, "CHECKED")
        self.assertEqual(model.steps[4].locatorEvidence[0].strategy, "testId")
        self.assertEqual(model.steps[4].assertions[0].type, "VALUE")
        self.assertEqual(model.steps[4].assertions[0].expected, "42")
        self.assertEqual(model.steps[5].locatorEvidence[0].strategy, "placeholder")
        self.assertEqual(model.steps[6].locatorEvidence[0].strategy, "text")
        self.assertTrue(all(e.confidence == "RECORDED" for s in model.steps for e in s.locatorEvidence))

    def test_playwright_css_testid_and_url(self):
        model = PlaywrightInputAdapter().from_text('''await page.goto('https://example.test');
await page.locator('[data-testid="save-button"]').click();
await expect(page).toHaveURL('https://example.test/done');''')
        self.assertEqual(model.steps[0].actions[0].type, "NAVIGATE")
        self.assertEqual(model.steps[1].locatorEvidence[0].strategy, "testId")
        self.assertEqual(model.steps[1].assertions[0].type, "URL")

    def test_playwright_popup_page_alias(self):
        model = PlaywrightInputAdapter().from_text('''const popup = await page.waitForEvent('popup');
await popup.getByRole('button', { name: 'Save' }).click();
await expect(popup).toHaveURL('https://example.test/done');''')
        self.assertEqual(model.steps[0].actions[0].type, "SWITCH_PAGE")
        self.assertEqual(model.steps[0].actions[0].pageAlias, "popup")
        self.assertEqual(model.steps[1].actions[0].pageAlias, "popup")
        self.assertEqual(model.steps[1].assertions[0].type, "URL")

    def test_id_only_actions_have_distinct_feature_steps(self):
        model = PlaywrightInputAdapter().from_text("""await page.locator('#first-button').click();
await page.locator('#second-button').click();""")
        self.assertNotEqual(feature_steps(model)[0].text, feature_steps(model)[1].text)

    def test_assertion_only_recording_does_not_invent_action_binding(self):
        model = PlaywrightInputAdapter().from_text(
            "await expect(page.getByRole('dialog')).toBeVisible();")
        self.assertEqual([step.kind for step in feature_steps(model)], ["expectedResult"])

    def test_malformed_and_unsupported_inputs_fail_closed(self):
        with self.assertRaises(UnsupportedInputError):
            MasyonInputAdapter().from_mapping({"jiraKey": "WBT-4", "steps": [{"action": "x"}]})
        for text in ("await page.evaluate(() => 1);", "await page.locator(`#${id}`).click();",
                     "await page.locator('#ember-123456').click();"):
            with self.subTest(text=text), self.assertRaises(UnsupportedInputError):
                PlaywrightInputAdapter().from_text(text)
        with tempfile.TemporaryDirectory() as directory:
            malformed = Path(directory) / "canonical.json"
            malformed.write_text(json.dumps({"modelVersion": 1, "steps": [{}]}))
            with self.assertRaises(UnsupportedInputError):
                normalize(malformed)
            unsupported_version = Path(directory) / "version.json"
            unsupported_version.write_text(json.dumps({"modelVersion": 2, "sourceType": "MASYON",
                                                       "testCaseId": "WBT-4", "steps": []}))
            with self.assertRaises(UnsupportedInputError):
                normalize(unsupported_version)
            malformed_masyon = Path(directory) / "masyon.json"
            malformed_masyon.write_text("{")
            with self.assertRaises(UnsupportedInputError):
                normalize(malformed_masyon, "masyon")

    def test_locator_ranking_preserves_all_evidence(self):
        evidence = [LocatorEvidence("testId", "save-button", "RECORDED"),
                    LocatorEvidence("id", "save-button", "RECORDED"),
                    LocatorEvidence("css", ".save", "PROVEN"),
                    LocatorEvidence("id", "ember-123456", "INFERRED")]
        index = {"locators": [{"pageClass": "SavePage", "field": "saveButton",
                               "kind": "id", "value": "save-button", "stable": True,
                               "source": "SavePage.java", "origin": "By"}]}
        ranked = locator_evidence("Save butonuna tıklanır", index, [], evidence)["all"]
        self.assertEqual([x["confidence"] for x in ranked],
                         ["PROVEN", "RECORDED", "RECORDED", "REPOSITORY"])
        self.assertEqual([x["kind"] for x in ranked[1:3]], ["id", "testId"])

    def test_auto_detection_and_equivalent_actions(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            masyon = folder / "WBT-5.json"
            masyon.write_text(json.dumps({"jiraKey": "WBT-5", "steps": [{"stepNumber": 1,
                "description": "Save butonuna tıklanır", "actions": [{"type": "CLICK",
                    "target": {"role": "button", "name": "Save"}}]}]}))
            playwright = folder / "WBT-5.ts"
            playwright.write_text("await page.getByRole('button', { name: 'Save' }).click();")
            self.assertEqual(detect_input_type(masyon), "masyon")
            self.assertEqual(detect_input_type(playwright), "playwright")
            a, b = normalize(masyon), normalize(playwright)
            self.assertEqual(a.steps[0].actions[0].type, b.steps[0].actions[0].type)
            self.assertEqual(a.steps[0].actions[0].target, b.steps[0].actions[0].target)
            self.assertEqual(feature_steps(a)[0].text, feature_steps(b)[0].text)
            exported = export_canonical(b, folder / "canonical")
            self.assertEqual(detect_input_type(exported), "canonical")
            self.assertEqual(normalize(exported).to_dict(), b.to_dict())
            ambiguous = folder / "unknown.txt"
            ambiguous.write_text("hello")
            with self.assertRaises(UnsupportedInputError):
                detect_input_type(ambiguous)


if __name__ == "__main__":
    unittest.main()
