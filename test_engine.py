import concurrent.futures
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from engine import Engine, Problem
from server import make_server

class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'test.sqlite3'
        self.e=Engine(self.path);self.e.seed();self.seq=0
    def tearDown(self):self.temp.cleanup()
    def act(self, action, data):
        self.seq+=1;return self.e.mutate(action,data,'test-'+str(self.seq))
    def op(self, ident=1):return next(o for o in self.e.workspace()['opportunities'] if o['id']==ident)
    def update(self, **changes):
        op=self.op();return self.act('update',dict(id=op['id'],version=op['version'],**changes))
    def agreement(self):
        op=self.op();return self.act('issue',dict(id=op['id'],version=op['version']))
    def complete(self, a):
        op=self.op();return self.act('complete',dict(id=op['id'],version=op['version'],agreement_id=a['id'],digest=a['digest']))
    def test_forecast_independently_reconciles_seeded_values(self):
        r=self.e.report('2026-10-28')
        self.assertEqual((r['open_cents'],r['weighted_cents'],r['won_cents'],r['overdue']),(19400000,9500000,0,2))
        self.assertEqual(sum(a['open_cents'] for a in r['accounts']),r['open_cents'])
    def test_forecast_rounds_each_opportunity_to_integer_cents(self):
        for i in range(2):self.act('opportunities',dict(account_id=1,contact_id=1,title=f'Small{i}',amount_cents=5,next_action='Check',due_date='2026-11-01'))
        self.assertEqual(self.e.report()['weighted_cents'],9500002)
    def test_retry_is_persistent_across_engine_reopen(self):
        payload=dict(account_id=1,name='New Contact',email='new@northstar.test')
        a=self.e.mutate('contacts',payload,'same-request')
        b=Engine(self.path).mutate('contacts',payload,'same-request')
        self.assertEqual(a,b);self.assertEqual(len(self.e.workspace()['contacts']),4)
    def test_request_key_rejects_changed_content(self):
        self.e.mutate('note',dict(id=1,detail='first'),'unique-key')
        with self.assertRaises(Problem) as ctx:self.e.mutate('note',dict(id=1,detail='different'),'unique-key')
        self.assertEqual(ctx.exception.status,409)
        self.assertEqual(len([a for a in self.e.workspace()['activities'] if a['kind']=='note']),1)
    def test_cross_account_contact_cannot_be_attached(self):
        with self.assertRaises(Problem):self.act('opportunities',dict(account_id=2,contact_id=1,title='Mismatch',amount_cents=100,next_action='Call',due_date='2026-11-01'))
        self.assertEqual(len(self.e.workspace()['opportunities']),6)
    def test_email_uniqueness_is_case_insensitive(self):
        with self.assertRaises(Problem):self.act('contacts',dict(account_id=2,name='Duplicate',email='MINA@NORTHSTAR.TEST'))
    def test_won_requires_completed_current_agreement(self):
        with self.assertRaises(Problem):self.update(stage='Won')
        a=self.agreement();self.complete(a);self.update(stage='Won')
        r=self.e.report();self.assertEqual(r['won_cents'],4800000);self.assertEqual(r['open_cents'],14600000)
    def test_stale_agreement_cannot_complete_after_amount_change(self):
        a=self.agreement();self.update(amount_cents=5000000)
        with self.assertRaises(Problem):self.complete(a)
        self.assertEqual(self.e.workspace()['agreements'][0]['status'],'draft')
    def test_completed_agreement_does_not_cover_changed_terms(self):
        a=self.agreement();self.complete(a);self.update(amount_cents=5000000)
        with self.assertRaises(Problem):self.update(stage='Won')
    def test_cannot_change_terms_while_closing(self):
        a=self.agreement();self.complete(a)
        with self.assertRaises(Problem):self.update(stage='Won',amount_cents=1)
        self.assertEqual(self.op()['stage'],'Proposal')
    def test_wrong_digest_and_cross_op_agreement_rejected(self):
        a=self.agreement()
        with self.assertRaises(Problem):self.act('complete',dict(id=1,version=self.op()['version'],agreement_id=a['id'],digest='wrong'))
        with self.assertRaises(Problem):self.act('complete',dict(id=4,version=self.op(4)['version'],agreement_id=a['id'],digest=a['digest']))
    def test_competing_edit_has_exactly_one_winner(self):
        op=self.op()
        def edit(i):
            try:return self.e.mutate('update',dict(id=1,version=op['version'],next_action=f'Edit{i}'),f'concurrent-{i}')['version']
            except Problem as exc:return exc.status
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(edit,[1,2]))
        self.assertEqual(sorted(results),[op['version']+1,409]);self.assertEqual(self.op()['version'],op['version']+1)
    def test_audit_failure_rolls_back_business_change_and_request(self):
        old=self.op();original=self.e.activity
        def fail(*args):raise RuntimeError('Simulated audit storage failure')
        self.e.activity=fail
        with self.assertRaises(RuntimeError):self.e.mutate('update',dict(id=1,version=old['version'],next_action='Not committed'),'rollback-key')
        self.assertEqual(old,self.op());self.e.activity=original
        saved=self.e.mutate('update',dict(id=1,version=old['version'],next_action='Now committed'),'rollback-key')
        self.assertEqual(saved['version'],old['version']+1)
    def test_invalid_stage_amount_and_date_do_not_mutate(self):
        old=self.op()
        for changes in [dict(stage=['Won']),dict(amount_cents=-1),dict(amount_cents=1.5),dict(amount_cents=True),dict(amount_cents=10**30),dict(due_date='2026-02-30')]:
            with self.assertRaises(Problem):self.update(**changes)
        self.assertEqual(old,self.op())
    def test_stage_skipping_rejected(self):
        with self.assertRaises(Problem):self.act('update',dict(id=3,version=1,stage='Won'))
    def test_csv_keeps_data_but_neutralizes_spreadsheet_formulas(self):
        self.act('opportunities',dict(account_id=1,contact_id=1,title='=2+2',amount_cents=125,next_action=' @formula',due_date='2026-11-01'))
        exported=self.e.csv();self.assertIn("'=2+2",exported);self.assertIn("'@formula",exported);self.assertIn('1.25',exported)
    def test_sql_metacharacters_are_stored_as_data(self):
        a=self.act('accounts',dict(name="'; DROP TABLE contacts;--",domain='literal.test'))
        self.assertIn('DROP TABLE',a['name']);self.assertEqual(len(self.e.workspace()['contacts']),3)

class HTTPTests(unittest.TestCase):
    def setUp(self):
        WorkflowTests.setUp(self);self.server=make_server(self.e,0);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.url=f'http://127.0.0.1:{self.server.server_port}'
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();WorkflowTests.tearDown(self)
    def call(self,path,body=None,headers=None):
        data=None if body is None else json.dumps(body).encode()
        h={'Content-Type':'application/json','Idempotency-Key':'http-1',**(headers or {})}
        req=urllib.request.Request(self.url+path,data=data,headers=h)
        try:
            with urllib.request.urlopen(req) as response:return response.status,response.read()
        except urllib.error.HTTPError as e:return e.code,e.read()
    def test_http_create_replay_and_stale_response(self):
        status,raw=self.call('/api/contacts',dict(account_id=1,name='HTTP Contact',email='http@northstar.test'))
        self.assertEqual(status,200)
        status2,raw2=self.call('/api/contacts',dict(account_id=1,name='HTTP Contact',email='http@northstar.test'))
        self.assertEqual(status2,200);self.assertEqual(json.loads(raw),json.loads(raw2))
        self.assertEqual(self.call('/api/update',dict(id=1,version=999,stage='Qualified'),{'Idempotency-Key':'stale'})[0],409)
    def test_http_rejects_cross_origin_and_bad_host(self):
        self.assertEqual(self.call('/api/workspace',headers={'Origin':'https://foreign.example'})[0],403)
        self.assertEqual(self.call('/api/workspace',headers={'Host':'foreign.example'})[0],403)
    def test_http_routes_and_read_contract(self):
        self.assertEqual(self.call('/api/report')[0],200)
        self.assertEqual(self.call('/../../engine.py')[0],404)
        self.assertIn(b'Relay CRM',self.call('/')[1])
    def test_http_requires_idempotency_and_json_object(self):
        self.assertEqual(self.call('/api/note',dict(id=1,detail='x'),{'Idempotency-Key':''})[0],422)
        self.assertEqual(self.call('/api/note',[])[0],422)

if __name__=='__main__':unittest.main(verbosity=2)
