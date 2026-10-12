import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from google.genai import types

from src.config import Config
from src.gemini.client import GeminiClient
from src.gemini.context import StepContext
from src.jira.models import Step
from src.recording.service import write_json
from src.runner.step_runner import run_step, run_fallback_step
from src.security.secrets import Secrets
from src.state.execution_state import ExecutionState

def config(): return Config(['node'],'https://example.test',[],10,5,5)
def fc(name,args): return types.Part(function_call=types.FunctionCall(name=name,args=args))
def model(*parts): return types.Content(role='model',parts=list(parts))
def dump(contents): return '\n'.join(c.model_dump_json() for c in contents)

class ModelTests(unittest.IsolatedAsyncioTestCase):
    async def test_configured_model_is_sent_to_sdk(self):
        with patch.dict(os.environ, {'GEMINI_MODEL':'custom-model'}), patch('src.gemini.client.genai.Client') as sdk:
            response=SimpleNamespace(candidates=[SimpleNamespace(content=model(fc('finish_step',{})))],usage_metadata=None)
            sdk.return_value.aio.models.generate_content=AsyncMock(return_value=response)
            ai=GeminiClient('unit-test-key',[],ExecutionState(),Config.from_env().gemini_model)
            await ai.request([])
            self.assertEqual(sdk.return_value.aio.models.generate_content.call_args.kwargs['model'],'custom-model')

    def test_default_model_without_env(self):
        with patch.dict(os.environ,{},clear=True): self.assertEqual(Config.from_env().gemini_model,'gemini-3.8-flash')

class ContextTests(unittest.TestCase):
    def test_semantic_resolution_candidates_reach_planner_without_a_snapshot(self):
        ctx=StepContext(Step(1,'Choose'),config(),Secrets([]))
        reply=ctx.observe('browser_semantic_plan',{}, {'needsDecision':True,'denied':True,
            'candidates':[{'role':'button','name':'Save'}], 'inventoryCount':4})
        self.assertEqual(reply['candidates'][0]['name'],'Save')
        self.assertNotIn('latestInspection',dump(ctx.request(ExecutionState(),{})))

    def test_only_latest_snapshot_and_exchange_survive(self):
        ctx=StepContext(Step(1,'Act'),config(),Secrets([])); state=ExecutionState()
        for label in ['OLD_SNAPSHOT','NEW_SNAPSHOT']:
            reply=ctx.observe('browser_snapshot',{}, {'snapshot':label,'isError':False})
            ctx.remember(model(fc('browser_snapshot',{})),[types.Part.from_function_response(name='browser_snapshot',response=reply)])
        text=dump(ctx.request(state,{}))
        self.assertNotIn('OLD_SNAPSHOT',text); self.assertEqual(text.count('NEW_SNAPSHOT'),1)
        ctx.observe('browser_click',{}, {'mutating':True,'executed':True})
        self.assertNotIn('NEW_SNAPSHOT',dump(ctx.request(state,{})))
        ctx.observe('browser_snapshot',{}, {'snapshot':'STALE_BEFORE_ERROR'})
        ctx.observe('browser_snapshot',{}, {'isError':True,'recoverable':True})
        self.assertNotIn('STALE_BEFORE_ERROR',dump(ctx.request(state,{})))

    def test_unchanged_snapshot_keeps_last_inspection_once(self):
        ctx=StepContext(Step(3,'Open SL Codes'),config(),Secrets([]))
        ctx.observe('browser_snapshot',{}, {'snapshot':'SL_CODES_PANEL'})
        response=ctx.observe('browser_snapshot',{}, {'unchanged':True,'reason':'UNCHANGED'})
        self.assertTrue(response['unchanged'])
        self.assertEqual(dump(ctx.request(ExecutionState(),{})).count('SL_CODES_PANEL'),1)

    def test_new_step_preserves_compact_state_not_old_case_or_history(self):
        state=ExecutionState(generated_values={'slCode':'AUTO_1'},completed_steps=[1],active_page='page1',
            pages=[{'alias':'page','url':'https://a','closed':False},
                   {'alias':'page1','url':'https://b','opener':'page','closed':False}])
        old=StepContext(Step(1,'OLD_STEP_ACTION'),config(),Secrets([]))
        old.observe('browser_snapshot',{}, {'snapshot':'OLD_DOM_PAYLOAD'})
        new=StepContext(Step(2,'NEW_STEP_ACTION'),config(),Secrets([]))
        text=dump(new.request(state,{}))
        self.assertNotIn('OLD_STEP_ACTION',text); self.assertNotIn('OLD_DOM_PAYLOAD',text)
        compact=state.compact()
        self.assertEqual(compact['generatedValues']['slCode'],'AUTO_1'); self.assertEqual(compact['activePage'],'page1')
        self.assertEqual(compact['knownPages'][1]['opener'],'page'); self.assertEqual(compact['completedSteps'],[1])

    def test_signature_preserved_and_state_not_duplicated_in_initial_prompt(self):
        ctx=StepContext(Step(1,'Act'),config(),Secrets([]))
        original=model(types.Part(function_call=types.FunctionCall(name='browser_snapshot',args={}),thought_signature=b'opaque-signature'))
        ctx.remember(original,[types.Part.from_function_response(name='browser_snapshot',response={'isError':False})])
        request=ctx.request(ExecutionState(generated_values={'code':'UNIQUE_VALUE'}),{})
        self.assertIs(request[-2],original); self.assertEqual(dump(request).count('UNIQUE_VALUE'),1)

    def test_utf8_output_and_diagnostic_redaction(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'execution.json'
            write_json(path,{'verification':'Uygulamaya başarıyla giriş yapıldı'},Secrets([]))
            self.assertIn('başarıyla',path.read_text()); self.assertNotIn('\\u',path.read_text())
        error=Secrets([]).diagnostic(ValueError('quota exceeded password="secret phrase" https://test/?session_id=abc'))
        self.assertIn('quota exceeded',error['error']); self.assertNotIn('secret phrase',error['error']); self.assertNotIn('=abc',error['error'])

class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def execute(self, responses, browser_results):
        class AI:
            async def request(self, _): return responses.pop(0)
        class Browser:
            tools=[{'name':name} for name in ['browser_snapshot','browser_click','browser_assert','browser_type']]
            calls=[]
            async def call(self,name,args): self.calls.append(name); return browser_results.pop(0)
        browser=Browser(); logs=[]; state=ExecutionState()
        result=await run_fallback_step(Step(5,'Fill dialog','','Fields filled'),AI(),browser,state,config(),Secrets([]),logs.append)
        return result,browser.calls,logs,state

    async def test_recoverable_inspection_does_not_fail_step(self):
        result,calls,logs,state=await self.execute([
            model(fc('browser_snapshot',{})),model(fc('browser_assert',{})),
            model(fc('finish_step',{'status':'COMPLETED','reason':'Filled','verification':'Fields verified','evidenceIds':['a']}))],
            [{'isError':True,'recoverable':True,'error':'Temporary inspection','errorClass':'SnapshotError'},
             {'executed':True,'evidenceId':'a','assertion':{'kind':'value'},'activePage':'page1'}])
        self.assertEqual(result['status'],'COMPLETED'); self.assertEqual(state.completed_steps,[5])
        self.assertTrue(any('Recoverable' in line for line in logs))

    async def test_semantic_plan_preserves_multiple_assertion_evidence_ids(self):
        class AI:
            def __init__(self): self.turn = 0
            async def plan_step(self, step, state, base_url, credential_names):
                return {'actions':[{'type':'click','target':{'name':'Tab'}}],
                        'expectedAssertions':[{'type':'visible','target':{'name':'Panel'}},
                                              {'type':'selected','target':{'name':'Tab'}}]}
            async def request(self, _):
                self.turn += 1
                if self.turn == 1:
                    return model(fc('browser_semantic_plan',{'actions':[{'type':'click','target':{'name':'Tab'}},
                        {'type':'assert','kind':'visible','target':{'name':'Panel'}}]}))
                return model(fc('finish_step',{'status':'COMPLETED','reason':'Verified',
                    'verification':'Both expected UI conditions passed','evidenceIds':['a','b']}))
        class Browser:
            tools=[{'name':'browser_semantic_plan'}]
            async def call(self, name, args):
                return {'executed':True,'mutating':True,'evidenceIds':['a','b'],
                    'assertions':[{'kind':'visible'},{'kind':'selected'}],'activePage':'page'}
        result=await run_step(Step(3,'Open tab','','Panel visible'),AI(),Browser(),ExecutionState(),
                              config(),Secrets([]),lambda _:None)
        self.assertEqual(result['status'],'COMPLETED')
        self.assertEqual([entry['id'] for entry in result['evidence']],['a','b'])

    async def test_model_multicall_stops_after_transition(self):
        result,calls,_,_=await self.execute([
            model(fc('browser_click',{}),fc('browser_type',{'text':'MUST_NOT_RUN'})),
            model(fc('finish_step',{'status':'BLOCKED','reason':'Replan needed','evidenceIds':[]}))],
            [{'executed':True,'mutating':True,'transition':True}])
        self.assertEqual(calls,['browser_click']); self.assertEqual(result['status'],'BLOCKED')

    async def test_assertion_failure_still_fails_with_diagnostics(self):
        result,_,_,_=await self.execute([model(fc('browser_assert',{}))],
            [{'isError':True,'error':'Expected field value did not match','errorClass':'AssertionError','activePage':'page1'}])
        self.assertEqual(result['status'],'FAILED'); self.assertEqual(result['errorDetails']['stepNumber'],5)
        self.assertEqual(result['errorDetails']['activePage'],'page1')

    async def test_unresolved_stale_ref_blocks_without_claiming_assertion_failure(self):
        result,_,_,_=await self.execute([model(fc('browser_assert',{}))],
            [{'isError':True,'recoveryIncomplete':True,'error':'Target could not be resolved uniquely in fresh page snapshot',
              'errorClass':'AssertionRecoveryError','activePage':'page1'}])
        self.assertEqual(result['status'],'BLOCKED')
        self.assertEqual(result['errorDetails']['stepNumber'],5)

    async def test_repeated_unchanged_inspection_stops_before_tool_limit(self):
        result,calls,logs,transition=await self.execute([
            model(fc('browser_snapshot',{})),model(fc('browser_snapshot',{})),
            model(fc('browser_snapshot',{}))],
            [{'snapshot':'Current fields','activePage':'page1'},
             {'unchanged':True,'reason':'UNCHANGED: no browser state change','activePage':'page1'},
             {'unchanged':True,'reason':'UNCHANGED: no browser state change','activePage':'page1'}])
        self.assertEqual(result['status'],'BLOCKED')
        self.assertEqual(calls,['browser_snapshot']*3)
        self.assertIn('Expected result not verified',result['reason'])
        self.assertNotIn('tool-call limit',result['reason'])

if __name__=='__main__': unittest.main()
