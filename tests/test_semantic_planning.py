import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from google.genai import types

from src.config import Config
from src.gemini.client import GeminiClient
from src.gemini.semantic_plan import validate_plan
from src.jira.models import Step
from src.runner.step_runner import run_step
from src.security.secrets import Secrets
from src.state.execution_state import ExecutionState


def config():
    return Config(['node'], 'https://example.test', [], 10, 5, 5)


class PlannerFirstTests(unittest.IsolatedAsyncioTestCase):
    async def run_action(self, action):
        order, logs = [], []
        class AI:
            async def plan_step(self, step, state, base_url, credentials):
                order.append('plan')
                return {'actions':[action], 'expectedAssertions':[
                    {'type':'visible','target':{'name':'Confirmation','roleHint':'status'}}]}
            async def request(self, _):
                raise AssertionError('Fallback model must not run on a resolved semantic action')
        class Browser:
            tools=[{'name':'poc_step_start'},{'name':'browser_semantic_plan'},{'name':'browser_snapshot'}]
            async def call(self, name, args):
                order.append(name)
                if name == 'poc_step_start': return {'activePage':'page','pages':[]}
                if name == 'browser_semantic_plan':
                    self.actions = args['actions']
                    return {'isError':False,'executed':True,'mutating':True,
                            'evidenceIds':['verified-1'],'assertions':[{'kind':'visible'}],
                            'batchExecuted':2,'activePage':'page'}
                raise AssertionError('Snapshot requested before resolver failure')
        browser=Browser()
        result=await run_step(Step(4,'Perform action','','Confirmation visible'),AI(),browser,
                              ExecutionState(),config(),Secrets([]),logs.append)
        self.assertEqual(result['status'],'COMPLETED',result)
        self.assertEqual(order,['poc_step_start','plan','browser_semantic_plan'])
        self.assertEqual(browser.actions[0],action)
        self.assertEqual(browser.actions[1]['type'],'assert')
        self.assertTrue(any(line.startswith('[PLAN] Step 4:') for line in logs))

    async def test_click_always_enters_planner_before_browser_tools(self):
        await self.run_action({'type':'click','target':{'name':'Open','roleHint':'button'}})

    async def test_fill_always_enters_planner_before_browser_tools(self):
        await self.run_action({'type':'fill','target':{'name':'Description'},'value':'Sample'})

    async def test_select_always_enters_planner_before_browser_tools(self):
        await self.run_action({'type':'select','target':{'name':'Priority'},'value':'High'})

    async def test_model_cannot_snapshot_instead_of_submitting_a_plan(self):
        class AI:
            async def plan_step(self, *_):
                raise ValueError('Planner did not return a SemanticPlan')
            async def request(self, _):
                raise AssertionError('Unplanned fallback must not start')
        class Browser:
            tools=[{'name':'browser_semantic_plan'},{'name':'browser_snapshot'}]
            async def call(self, *_):
                raise AssertionError('Browser cannot be called without a plan')
        result=await run_step(Step(1,'Open','','Visible'),AI(),Browser(),ExecutionState(),
                              config(),Secrets([]),lambda _:None)
        self.assertEqual(result['status'],'BLOCKED')
        self.assertIn('Semantic planning failed',result['reason'])

    async def test_snapshot_fallback_only_after_structured_no_match(self):
        order=[]
        class AI:
            async def plan_step(self, *_):
                order.append('plan')
                return {'actions':[{'type':'click','target':{'name':'Open','roleHint':'button'}}],
                        'expectedAssertions':[{'type':'visible','target':{'name':'Dialog','roleHint':'dialog'}}]}
            async def request(self, _):
                order.append('fallback-model')
                return types.Content(role='model',parts=[types.Part(function_call=types.FunctionCall(
                    name='browser_snapshot',args={}))])
        class Browser:
            tools=[{'name':'browser_semantic_plan'},{'name':'browser_snapshot'}]
            async def call(self, name, _):
                order.append(name)
                if name == 'browser_semantic_plan':
                    return {'isError':False,'denied':True,'resolverStatus':'NO_MATCH',
                            'reason':'No candidate','candidates':[],'executed':False}
                return {'isError':False,'unchanged':True,'reason':'UNCHANGED'}
        result=await run_step(Step(1,'Open','','Dialog visible'),AI(),Browser(),ExecutionState(),
                              Config(['node'],'',[],4,5,5),Secrets([]),lambda _:None)
        self.assertEqual(order[:4],['plan','browser_semantic_plan','fallback-model','browser_snapshot'])
        self.assertEqual(result['status'],'BLOCKED')


class PlanValidationTests(unittest.TestCase):
    def test_plan_rejects_selector_only_targets(self):
        with self.assertRaises(ValueError):
            validate_plan({'actions':[{'type':'click','target':{'selector':'#save'}}],
                           'expectedAssertions':[]},'')


class PlannerToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_planner_model_can_only_submit_a_semantic_plan(self):
        supplied={'actions':[{'type':'click','target':{'name':'Open','roleHint':'button'}}],
                  'expectedAssertions':[{'type':'visible','target':{'name':'Dialog','roleHint':'dialog'}}]}
        answer=types.Content(role='model',parts=[types.Part(function_call=types.FunctionCall(
            name='submit_semantic_plan',args=supplied))])
        with patch('src.gemini.client.genai.Client') as sdk:
            sdk.return_value.aio.models.generate_content=AsyncMock(return_value=SimpleNamespace(
                candidates=[SimpleNamespace(content=answer)],usage_metadata=None))
            ai=GeminiClient('fake-key',[],ExecutionState(),'configured-model')
            result=await ai.plan_step(Step(1,'Open','','Dialog visible'),{},'',[])
            self.assertEqual(result,supplied)
            call=sdk.return_value.aio.models.generate_content.call_args
            names=[tool.name for group in call.kwargs['config'].tools for tool in group.function_declarations]
            self.assertEqual(names,['submit_semantic_plan'])
