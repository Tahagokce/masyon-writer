"""Deterministic payload-size comparison, NOT a Gemini token benchmark."""
import json
from dataclasses import asdict
from google.genai import types
from src.config import Config
from src.gemini.context import StepContext, content
from src.jira.models import Step
from src.security.secrets import Secrets
from src.state.execution_state import ExecutionState


def measure():
    step=Step(5,'Fill modal','', 'Fields contain expected values')
    config=Config(['node'],'https://example.test',[],30,300,60)
    state=ExecutionState(generated_values={'slCode':'AUTO_FIXED'},active_page='page1',
        pages=[{'alias':'page','url':'https://example.test','closed':False},
               {'alias':'page1','url':'https://example.test/detail','opener':'page','closed':False}])
    context=StepContext(step,config,Secrets([]))
    initial=content({'currentStep':asdict(step),'generatedValues':state.generated_values,
        'previousState':state.summary,'pages':state.pages,'baseUrl':config.base_url,'credentialNames':[]})
    old_history=[]
    old_chars=new_chars=0
    for index in range(12):
        recent=old_history[-4:]
        while len(recent)>1 and sum(len(c.model_dump_json()) for pair in recent for c in pair)>20000:
            recent=recent[1:]
        old=[initial,content({'generatedValues':state.generated_values,'pages':state.pages,'verificationEvidence':[]}),
             *[c for pair in recent for c in pair]]
        new=context.request(state,{})
        old_chars+=sum(len(c.model_dump_json()) for c in old)
        new_chars+=sum(len(c.model_dump_json()) for c in new)
        name='browser_snapshot' if index%2==0 else 'browser_type'
        response={'isError':False,'activePage':'page1','pages':state.pages}
        if name=='browser_snapshot': response['snapshot']=f'SNAPSHOT_{index}:'+'x'*3600
        else: response.update(mutating=True,executed=True)
        model=types.Content(role='model',parts=[types.Part(function_call=types.FunctionCall(name=name,args={}))])
        old_reply=types.Content(role='user',parts=[types.Part.from_function_response(name=name,response=response)])
        old_history.append((model,old_reply))
        brief=context.observe(name,{},response)
        context.remember(model,[types.Part.from_function_response(name=name,response=brief)])
    return {'kind':'SERIALIZED_CONTEXT_CHARACTERS_ONLY','requests':12,'oldCharacters':old_chars,
            'newCharacters':new_chars,'reductionPercent':round((1-new_chars/old_chars)*100,2),
            'excludes':'system instructions and tool schemas; no live Gemini token saving claimed'}

if __name__=='__main__': print(json.dumps(measure(),indent=2))
