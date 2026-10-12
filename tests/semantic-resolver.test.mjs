import {test} from 'node:test';
import assert from 'node:assert/strict';
import {chooseCandidate,locatorStrategies,stableId} from '../bridge/locator-resolver.mjs';

const item = (overrides = {}) => ({id:'',testId:'',role:'',name:'',text:'',label:'',
  placeholder:'',visible:true,enabled:true,tag:'div',path:'html > body > div',...overrides});

test('rendered tab name selects a tab with an unrelated stable id', () => {
  const picked = chooseCandidate({name:'SL Codes',roleHint:'tab'},[
    item({id:'commodity-tab',role:'tab',name:'Commodity Subtypes'}),
    item({id:'special-load-tab',role:'tab',name:'SL Codes',text:'SL Codes'})]);
  assert.equal(picked.status,'resolved');
  assert.equal(picked.candidate.id,'special-load-tab');
  assert.equal(locatorStrategies(picked.candidate)[0].expression,'page.locator("#special-load-tab")');
});

test('button and labeled input resolve by visible semantics', () => {
  const inventory = [item({role:'button',name:'Save',text:'Save'}),
    item({role:'textbox',name:'Description',label:'Description',tag:'input'})];
  assert.equal(chooseCandidate({name:'Save',roleHint:'button'},inventory).candidate.role,'button');
  assert.equal(chooseCandidate({name:'Description'},inventory).candidate.tag,'input');
});

test('locator priority uses test ID, role, text and placeholder before CSS', () => {
  assert.equal(locatorStrategies(item({testId:'save-control',role:'button',name:'Save'}))[0].kind,'testId');
  assert.equal(locatorStrategies(item({role:'tab',name:'Codes'}))[0].kind,'roleName');
  assert.equal(locatorStrategies(item({text:'Save'}))[0].kind,'text');
  assert.equal(locatorStrategies(item({placeholder:'Description'}))[0].kind,'placeholder');
});

test('framework IDs are rejected and equivalent names match after normalization', () => {
  for (const id of ['cdk-overlay-123','nz-tabs-0-tab-1','auto-generated-12345','12345'])
    assert.equal(stableId(id),false,id);
  assert.equal(stableId('special-load-tab'),true);
  const picked = chooseCandidate({name:'Códés',roleHint:'tab'},[item({role:'tab',name:'Codes'})]);
  assert.equal(picked.status,'resolved');
});

test('equally strong matches are ambiguous instead of selecting at random', () => {
  const picked = chooseCandidate({name:'Save',roleHint:'button'},[
    item({role:'button',name:'Save',id:'first'}),item({role:'button',name:'Save',id:'second'})]);
  assert.equal(picked.status,'ambiguous');
  assert.equal(picked.candidates.length,2);
});
