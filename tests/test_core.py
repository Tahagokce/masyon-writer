import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.config import Config
from src.jira.parser import parse_csv
from src.security.secrets import Secrets
from src.state.execution_state import ExecutionState


class ParserTests(unittest.TestCase):
    def parse(self, content, name="WBT-10891.csv", key=None):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / name
            file.write_text(content, encoding="utf-8")
            return parse_csv(file, key)

    def test_bom_semicolon_multiline(self):
        case = self.parse('\ufeffAction;Data;Expected Result\n"Open\nmenu";"x;y";Visible\n')
        self.assertEqual(case.jiraKey, "WBT-10891")
        self.assertEqual(case.steps[0].action, "Open\nmenu")
        self.assertEqual(case.steps[0].data, "x;y")

    def test_alias_content_key(self):
        case = self.parse('Issue Key,Test Step,Test Data,Expected Results\nABC-12,Click,"a,b",Shown\n', 'case.csv')
        self.assertEqual(case.jiraKey, 'ABC-12')
        self.assertEqual(case.steps[0].data, 'a,b')

    def test_rejects_bad_rows_keys_and_numbers(self):
        for text in ['Action,Data\n,foo\n', 'Data\nx\n', 'Action,Step Number\nX,2\n',
                     'Action,Key\nX,A-1\nY,A-2\n', 'Action,Step\nX,Y\n', 'Action\nX,Y\n']:
            with self.subTest(text=text), self.assertRaises(ValueError): self.parse(text)
        with self.assertRaises(ValueError): self.parse('Action,Key\nX,A-1\n', key='../../outside')

    def test_csv_semicolon_inside_comma_header(self):
        case = self.parse('Action,Data,Expected Result,"Metadata; notes"\nOpen,"a;b",Visible,unused\n')
        self.assertEqual(case.steps[0].data, 'a;b')


class SecurityStateTests(unittest.TestCase):
    def test_secret_encodings_and_nested_output(self):
        with patch.dict(os.environ, {'WBS_PASSWORD':'private"pw&value'}):
            guard = Secrets(['WBS_PASSWORD'])
            for variant in guard.variants:
                self.assertNotIn(variant, guard.clean('before ' + variant + ' after'))
            self.assertEqual(guard.object({'x':['private"pw&value']}), {'x':['[REDACTED]']})

    def test_value_reuse_and_collision_avoidance(self):
        a, b = ExecutionState(), ExecutionState()
        self.assertEqual(a.generate('slCode'), a.generate('slCode'))
        self.assertNotEqual(a.generate('slCode'), b.generate('slCode'))
        with self.assertRaises(ValueError): a.generate('../../bad')
        self.assertEqual(a.generate('count', value='3'), '3')
        self.assertEqual(a.generate('count'), '3')
        with self.assertRaises(ValueError): a.generate('count', value='4')

    def test_command_is_argv_and_gemini_key_not_forwarded(self):
        from src.mcp.process import process_environment
        with patch.dict(os.environ, {'GEMINI_API_KEY':'private-api-key', 'WBS_PASSWORD':'secret',
                                    'LOG_LEVEL':'DEBUG', 'LOG_LOCATORS':'true'}):
            child = process_environment(['WBS_PASSWORD'])
            self.assertNotIn('GEMINI_API_KEY', child)
            self.assertEqual(child['LOG_LEVEL'], 'DEBUG')
            self.assertEqual(child['LOG_LOCATORS'], 'true')
        with patch.dict(os.environ, {'PLAYWRIGHT_MCP_COMMAND':'"npx @playwright/mcp"'}):
            with self.assertRaises(ValueError): Config.from_env()


class CompletionTests(unittest.IsolatedAsyncioTestCase):
    """Protocol unit tests only. Scripted responses do NOT qualify as Gemini acceptance."""
    async def execute(self, calls, expected='Welcome is visible'):
        from google.genai import types
        from src.runner.step_runner import run_fallback_step
        from src.jira.models import Step
        class FakeAI:
            async def request(self, contents):
                name, args = calls.pop(0)
                return types.Content(role='model', parts=[types.Part(function_call=types.FunctionCall(name=name,args=args))])
        class FakeBrowser:
            tools = [{'name':'browser_assert'}, {'name':'browser_click'}]
            async def call(self, name, args):
                if name == 'browser_assert':
                    return {'executed':True, 'evidenceId':'a1', 'assertion':args, 'activePage':'page'}
                return {'executed':True, 'mutating':True}
        config = Config(['node'], '', [], len(calls), 5, 5)
        return await run_fallback_step(Step(1,'Log in','',expected), FakeAI(), FakeBrowser(), ExecutionState(), config, Secrets([]), lambda _:None)

    async def test_false_success_is_rejected(self):
        result = await self.execute([('finish_step',{'status':'COMPLETED','reason':'done','evidenceIds':['invented'],'verification':'looks good'})])
        self.assertEqual(result['status'], 'BLOCKED')

    async def test_fresh_evidence_required(self):
        result = await self.execute([('browser_assert',{}), ('browser_click',{}),
            ('finish_step',{'status':'COMPLETED','reason':'done','evidenceIds':['a1'],'verification':'visible'})])
        self.assertEqual(result['status'], 'BLOCKED')

    async def test_verified_completion(self):
        result = await self.execute([('browser_click',{}),('browser_assert',{}),
            ('finish_step',{'status':'COMPLETED','reason':'done','evidenceIds':['a1'],'verification':'Welcome visible'})])
        self.assertEqual(result['status'], 'COMPLETED')

    async def test_ambiguity_blocks_without_browser_action(self):
        result = await self.execute([('finish_step',{'status':'BLOCKED','reason':'Business intent unclear','evidenceIds':[]})])
        self.assertEqual(result['status'], 'BLOCKED')


if __name__ == '__main__': unittest.main()
