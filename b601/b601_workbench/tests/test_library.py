import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import library

class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.patch=patch.object(library,'DB',Path(self.temp.name)/'catalog.sqlite3');self.patch.start();self.addCleanup(self.patch.stop)
        self.a=library.create('任务 A','Task A')['id'];self.b=library.create('任务 B','Task B')['id']
        library.add_members(self.a,'raw_a',[{'index':0,'tasks':['Task A'],'frames':30},{'index':1,'tasks':['Task A'],'frames':40}])
        library.add_members(self.b,'raw_b',[{'index':0,'tasks':['Task B'],'frames':50}])
    def update(self,name,index=0,**fields):return library.update({'dataset':name,'episode':index,**fields})
    def describe(self,name,index):return {'signature':{'fps':30},'episode':{'frames':30},'replay':'unverified'}
    def test_move_trash_restore_preserves_identity_and_notes(self):
        self.update('raw_a',note='抓取稳定',review='keep',collection=self.b,trashed=True)
        row=next(r for r in library.snapshot()['members'] if r['dataset']=='raw_a' and r['episode']==0)
        self.assertEqual(row['collection'],self.b);self.assertEqual(row['note'],'抓取稳定');self.assertTrue(row['trashed'])
        self.update('raw_a',trashed=False)
        self.assertEqual(library.add_members(self.a,'raw_a',[{'index':0,'tasks':[],'frames':30}])['added'],0)
        self.assertEqual(len(library.snapshot()['members']),3)
    def test_active_episode_cannot_be_modified(self):
        with self.assertRaises(ValueError):library.update({'dataset':'raw_a','episode':0,'trashed':True},'raw_a')
    def test_mixture_freezes_membership_and_normalizes_weights(self):
        self.update('raw_a',review='keep');self.update('raw_b',review='keep')
        item=library.mixture({'name':'组合 v1','selections':[{'id':self.a,'weight':2},{'id':self.b,'weight':3}]},self.describe)
        self.assertEqual(item['episodes'],2);self.assertEqual([s['probability'] for s in item['sources']],[.4,.6])
        self.update('raw_a',trashed=True);self.update('raw_b',collection=self.a,note='later')
        saved=library.snapshot()['mixtures'][0]
        self.assertEqual(saved,item);self.assertEqual(saved['status'],'draft')
    def test_mixture_excludes_trash_reject_and_unreviewed(self):
        self.update('raw_a',review='keep',trashed=True);self.update('raw_a',1,review='reject')
        with self.assertRaisesRegex(ValueError,'没有标记'):library.mixture({'name':'bad','selections':[{'id':self.a,'weight':1}]},self.describe)
        self.assertEqual(library.snapshot()['mixtures'],[])
    def test_incompatible_profiles_and_invalid_weights_rejected_atomically(self):
        self.update('raw_a',review='keep');self.update('raw_b',review='keep')
        choices=[{'id':self.a,'weight':1},{'id':self.b,'weight':1}]
        with self.assertRaisesRegex(ValueError,'不兼容'):library.mixture({'name':'bad','selections':choices},lambda name,index:{'signature':name})
        for weight in (0,-1,float('nan'),True):
            with self.assertRaises(ValueError):library.mixture({'name':'bad','selections':[{'id':self.a,'weight':weight}]},self.describe)
        with self.assertRaises(ValueError):library.mixture({'name':'bad','selections':[choices[0],choices[0]]},self.describe)
        self.assertEqual(library.snapshot()['mixtures'],[])
    def test_no_fake_replay_acceptance_or_unknown_fields(self):
        with self.assertRaises(ValueError):self.update('raw_a',review='replay_passed')
        with self.assertRaises(ValueError):self.update('raw_a',path='/tmp')
        with self.assertRaises(ValueError):library.collection(None)

    def test_capture_request_keeps_selected_group_and_task(self):
        import recording
        from unittest.mock import Mock
        jobs=Path(self.temp.name)/'jobs'
        config={'dataset_root':str(Path(self.temp.name)/'raw'),'lerobot_python':'unused'}
        with patch.object(recording,'JOBS',jobs), patch('can_control.read_state',return_value={'up':True,'bitrate':1000000,'bus_state':'ERROR-ACTIVE'}), patch.object(Path,'exists',return_value=True), patch.object(recording.subprocess,'Popen',return_value=Mock(pid=123)) as spawn:
            result=recording.start({'task':'Task A second attempt','seconds':0,'prepared':True,'collection':self.a},config)
        request=json.loads((jobs/result['id']/'request.json').read_text())
        self.assertEqual(request['collection'],self.a);self.assertEqual(request['task'],'Task A second attempt')
        member=next(m for m in library.snapshot()['members'] if m['dataset']==request['dataset'])
        self.assertEqual(member['collection'],self.a);self.assertEqual(member['episode'],0)
        self.assertEqual(spawn.call_count,1)

    def test_authenticated_library_http_flow(self):
        import app
        import threading
        from urllib.request import Request,urlopen
        from urllib.error import HTTPError
        server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        origin=f'http://127.0.0.1:{server.server_port}'
        def post(action,body,auth=True):
            headers={'Origin':origin,'X-Workbench-Token':app.CSRF_TOKEN} if auth else {}
            with urlopen(Request(origin+'/api/library/'+action,data=json.dumps(body).encode(),headers=headers,method='POST')) as response:return json.load(response)
        try:
            with self.assertRaises(HTTPError) as error:post('create',{'name':'HTTP','task':'Task'},False)
            self.assertEqual(error.exception.code,403)
            group=post('create',{'name':'HTTP','task':'Task'})
            with patch.object(app,'episodes',return_value=[{'index':0,'tasks':['Task'],'frames':30}]):
                self.assertEqual(post('import',{'collection':group['id'],'dataset':'fake'})['added'],1)
            post('update',{'dataset':'fake','episode':0,'review':'keep','note':'HTTP tested'})
            with patch.object(app,'describe_source',return_value=self.describe('fake',0)):
                draft=post('mixture',{'name':'HTTP draft','selections':[{'id':group['id'],'weight':1}]})
            self.assertEqual(draft['episodes'],1);self.assertEqual(draft['status'],'draft')
            post('update',{'dataset':'fake','episode':0,'trashed':True})
            post('update',{'dataset':'fake','episode':0,'trashed':False})
        finally:
            server.shutdown();server.server_close();thread.join()
