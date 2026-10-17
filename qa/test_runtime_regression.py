"""Independent mock-provider acceptance tests: no external requests or paid API."""
import sys
from _qa_paths import REPO, QA_OUTPUT, TOKENIZER
sys.path.insert(0,str(REPO))
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from wgforge import runtime as rt

class Response:
    def __init__(self, payload): self.raw=json.dumps(payload).encode()
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def read(self,limit): return self.raw[:limit]

class FakeOpener:
    def __init__(self,payloads): self.payloads=list(payloads); self.requests=[]
    def open(self,request,timeout=None):
        self.requests.append(json.loads(request.data))
        if not self.payloads: raise AssertionError('Unexpected provider call')
        p=self.payloads.pop(0)
        if isinstance(p,BaseException): raise p
        return Response(p)

def reply(name,args,i='call1'):
    return {'id':'mock-response','choices':[{'message':{'role':'assistant','content':None,'tool_calls':[{'id':i,'type':'function','function':{'name':name,'arguments':json.dumps(args)}}]},'finish_reason':'tool_calls'}], 'usage':{'prompt_tokens':100,'completion_tokens':100}}

def config():
    return {'approve_live':True,'endpoint':'https://qa.example.com/v1/chat/completions','allowed_hosts':['qa.example.com'],'api_key_env':'BRIEFCASE_QA_MOCK_KEY','model':'mock-only','privacy':{'public_upload_approved':True},'timeout_seconds':1,'max_retries':0,'budget':{'max_cost_usd':5,'input_usd_per_million':1,'output_usd_per_million':1,'max_requests':8,'max_steps':8,'max_input_tokens':200000,'max_output_tokens':1000,'max_total_tokens':800000}}

class RuntimeAcceptance(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.base=Path(self.tmp.name)
        self.task=self.base/'task'; (self.task/'public'/'anchors').mkdir(parents=True); (self.task/'private').mkdir()
        (self.task/'public'/'task.json').write_text(json.dumps({'task_id':'qa_task','prompt':'Read source.txt and write result.txt'}))
        (self.task/'public'/'anchors'/'source.txt').write_text('PUBLIC_CANARY_879')
        (self.task/'private'/'truth.json').write_text('GOLD_PRIVATE_CANARY_423')
        self.run=self.base/'run'; self.cfg=config()
        self.env=patch.dict(os.environ,{'BRIEFCASE_QA_MOCK_KEY':'fake-key-never-sent'}); self.env.start()
    def tearDown(self): self.env.stop(); self.tmp.cleanup()
    def execute(self,payloads,resume=False,cfg=None):
        fake=FakeOpener(payloads)
        with patch.object(rt.urllib.request,'build_opener',return_value=fake):
            result=rt.run_agent(self.task/'public',self.run,cfg or self.cfg,resume=resume)
        return result,fake
    def test_tool_loop_visibility_private_exclusion_resume(self):
        payloads=[reply('read_file',{'path':'input/anchors/source.txt'},'r'),reply('write_file',{'path':'output/result.txt','content':'Completed'},'w'),reply('finish',{'summary':'done'},'f')]
        result,fake=self.execute(payloads)
        self.assertTrue(result['completed']); self.assertEqual(len(fake.requests),3)
        self.assertIn('PUBLIC_CANARY_879',json.dumps(fake.requests[1]))
        self.assertNotIn('GOLD_PRIVATE_CANARY_423',json.dumps(fake.requests))
        self.assertNotIn('fake-key-never-sent',(self.run/'checkpoint.json').read_text())
        again,unused=self.execute([],resume=True)
        self.assertTrue(again['completed']); self.assertEqual(unused.requests,[])
    def test_no_shell_paths_symlinks_and_input_writes(self):
        sb=rt.Sandbox(self.base/'sandbox'); sb.copy_public(self.task/'public')
        for name,args in [('exec',{'cmd':'echo unsafe'}),('read_file',{'path':'/etc/passwd'}),('read_file',{'path':'input/../../task/private/truth.json'}),('read_file',{'path':'private/truth.json'}),('write_file',{'path':'input/anchors/source.txt','content':'tamper'})]:
            with self.subTest(name=name,args=args),self.assertRaises(rt.RuntimeErrorBase): sb.execute(name,args)
        (sb.root/'input'/'link').symlink_to(self.task/'private'/'truth.json')
        with self.assertRaises(rt.RuntimeErrorBase): sb.execute('read_file',{'path':'input/link'})
    def test_missing_finish_is_failed_run(self):
        p={'id':'mock','choices':[{'message':{'role':'assistant','content':'I did it'},'finish_reason':'stop'}],'usage':{'prompt_tokens':100,'completion_tokens':100}}
        with self.assertRaises(rt.RuntimeErrorBase): self.execute([p])
        self.assertFalse(json.loads((self.run/'run.json').read_text())['completed'])
    def test_malformed_provider_json_writes_failed_report(self):
        fake=FakeOpener([[]])
        with patch.object(rt.urllib.request,'build_opener',return_value=fake):
            with self.assertRaises(rt.RuntimeErrorBase): rt.run_agent(self.task/'public',self.run,self.cfg)
        self.assertFalse(json.loads((self.run/'run.json').read_text())['completed'])
    def test_step_budget(self):
        cfg=config(); cfg['budget']['max_steps']=1
        with self.assertRaises(rt.BudgetExceeded): self.execute([reply('read_file',{'path':'input/anchors/source.txt'},'r')],cfg=cfg)
        self.assertFalse(json.loads((self.run/'run.json').read_text())['completed'])
    def test_tool_error_is_visible_next_turn(self):
        result,fake=self.execute([reply('read_file',{'path':'input/../private/truth.json'},'bad'),reply('write_file',{'path':'output/result.txt','content':'safe'},'w'),reply('finish',{'summary':'done'},'f')])
        self.assertTrue(result['completed'])
        self.assertIn('forbidden',json.dumps(fake.requests[1]))
        self.assertNotIn('GOLD_PRIVATE_CANARY_423',json.dumps(fake.requests))
    def test_input_tamper_blocks_resume(self):
        self.execute([reply('write_file',{'path':'output/result.txt','content':'safe'},'w'),reply('finish',{'summary':'done'},'f')])
        (self.run/'workspace'/'input'/'source.txt').write_text('CHANGED')
        with self.assertRaises(rt.RuntimeErrorBase): self.execute([],resume=True)
    def test_output_tamper_blocks_completed_resume(self):
        self.execute([reply('write_file',{'path':'output/result.txt','content':'safe'},'w'),reply('finish',{'summary':'done'},'f')])
        (self.run/'workspace'/'output'/'result.txt').write_text('CHANGED')
        with self.assertRaises(rt.RuntimeErrorBase): self.execute([],resume=True)
    def test_existing_input_blocks_cold_start(self):
        p=self.run/'workspace'/'input';p.mkdir(parents=True);(p/'previous.txt').write_text('OLD_TASK_CANARY')
        with self.assertRaises(rt.RuntimeErrorBase): self.execute([])
    def test_request_budget_counts_failed_network_attempt(self):
        cfg=config(); cfg['budget']['max_requests']=1; cfg['max_retries']=1
        fake=FakeOpener([TimeoutError('timeout')])
        with patch.object(rt.urllib.request,'build_opener',return_value=fake),patch.object(rt.time,'sleep'):
            with self.assertRaises(rt.ProviderError): rt.run_agent(self.task/'public',self.run,cfg)
        self.assertEqual(len(fake.requests),1)
        report=json.loads((self.run/'run.json').read_text())
        self.assertFalse(report['completed']); self.assertGreater(report['cost']['charged_upper_bound_usd'],0)
    def test_cost_budget_stops_before_call(self):
        cfg=config(); cfg['budget']['max_cost_usd']=0.0000001
        fake=FakeOpener([])
        with patch.object(rt.urllib.request,'build_opener',return_value=fake):
            with self.assertRaises(rt.BudgetExceeded): rt.run_agent(self.task/'public',self.run,cfg)
        self.assertEqual(fake.requests,[])
    def test_inflight_request_not_retransmitted(self):
        with self.assertRaises(KeyboardInterrupt): self.execute([KeyboardInterrupt()])
        with self.assertRaises(rt.ProviderError): self.execute([],resume=True)
        state=json.loads((self.run/'checkpoint.json').read_text())
        self.assertEqual(len(state['requests']),1); self.assertEqual(state['requests'][0]['status'],'in_flight')
    def test_started_tool_not_reexecuted(self):
        fake=FakeOpener([reply('write_file',{'path':'output/result.txt','content':'safe'},'w')])
        with patch.object(rt.urllib.request,'build_opener',return_value=fake),patch.object(rt.Sandbox,'execute',side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt): rt.run_agent(self.task/'public',self.run,self.cfg)
        with self.assertRaises(rt.RuntimeErrorBase): self.execute([],resume=True)
        self.assertFalse((self.run/'workspace'/'output'/'result.txt').exists())
    def test_saved_success_response_recovered_without_second_request(self):
        fake=FakeOpener([reply('write_file',{'path':'output/result.txt','content':'safe'},'w')])
        with patch.object(rt.urllib.request,'build_opener',return_value=fake),patch.object(rt,'_assistant_message',side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt): rt.run_agent(self.task/'public',self.run,self.cfg)
        result,resumed=self.execute([reply('finish',{'summary':'done'},'f')],resume=True)
        self.assertTrue(result['completed']); self.assertEqual(len(resumed.requests),1)
        self.assertEqual((self.run/'workspace'/'output'/'result.txt').read_text(),'safe')
    def test_duplicate_tool_id_changed_args_rejected(self):
        with self.assertRaises(rt.ProviderError): self.execute([reply('read_file',{'path':'input/anchors/source.txt'},'same'),reply('write_file',{'path':'output/result.txt','content':'changed'},'same')])
        self.assertFalse((self.run/'workspace'/'output'/'result.txt').exists())

if __name__=='__main__': unittest.main(verbosity=2)
