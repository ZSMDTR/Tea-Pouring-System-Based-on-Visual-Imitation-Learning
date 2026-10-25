"""Replay isolation, acceptance and lifecycle tests using synthetic motors only."""
import fcntl
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch,Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app,library,recording,replay,replay_data as data,replay_worker as worker

LIMITS={n:(0,270) if n=='gripper' else (-145,200) for n in data.NAMES}


class Timer:
    def __init__(self):self.t=0;self.sleeps=[]
    def clock(self):return self.t
    def sleep(self,v):self.t+=v;self.sleeps.append(v)


class Robot:
    def __init__(self,folder,mode='normal'):
        self.folder=folder;self.mode=mode;self.events=[];self.sent=[];self.pos={n:0. for n in data.NAMES}
        self.config=SimpleNamespace(joint_limits=LIMITS);self.bus=None;self.cameras={};self._emergency_disable_requested=False
        self.motors={n:SimpleNamespace(get_state=lambda:object(),disable=lambda:self.events.append('disable'),close=lambda:self.events.append('close')) for n in data.NAMES}
    def connect(self,calibrate=False):
        assert not calibrate
        self.events.append('connect')
        if self.mode=='connect_error':raise RuntimeError('partial connect')
    def get_observation(self):return {n+'.pos':v for n,v in self.pos.items()}
    def send_action(self,action):
        self.events.append('send');self.sent.append(dict(action))
        if self.mode=='send_error':raise RuntimeError('CAN fault')
        self.pos={n:max(LIMITS[n][0],min(LIMITS[n][1],action[n+'.pos']*data.DIRECTIONS[n])) for n in data.NAMES}
        if self.mode=='manual' and len(self.sent)==2:(self.folder/'stop').touch()
        return {n+'.pos':v for n,v in self.pos.items()}
    def safe_zero(self,exit_on_complete=False):
        self.events.append('return')
        if self.mode=='return_error':raise RuntimeError('return fault')


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.jobs=self.root/'jobs';self.jobs.mkdir()
        self.patches=[patch.object(library,'DB',self.root/'library.sqlite3'),patch.object(recording,'JOBS',self.jobs)]
        for p in self.patches:p.start()
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()
    def rows(self):return [{'frame_index':i,'timestamp':i/30,'action':[1.,2.,-3.,4.,5.,6.,7.], 'observation.state':[0.]*7} for i in range(4)]
    def test_direction_and_gripper_mapping_exactly_once(self):
        actions,intervals,_,_=data.validate_rows(self.rows(),30,None,LIMITS,data.DIRECTIONS)
        self.assertEqual(actions[0]['raw']['gripper.pos'],7)
        self.assertEqual(actions[0]['motor']['gripper'],42)
        self.assertEqual(actions[0]['motor']['elbow_flex'],3)
        self.assertAlmostEqual(sum(intervals),4/30)
    def test_invalid_or_discontinuous_rows_blocked(self):
        for field,value in [('frame_index',8),('timestamp',-1),('action',[math.nan]*7),('action',[0.]*6),('observation.state',[math.inf]*7)]:
            rows=self.rows();rows[1][field]=value
            with self.subTest(field=field,value=value),self.assertRaises(ValueError):data.validate_rows(rows,30,None,LIMITS,data.DIRECTIONS)
        with self.assertRaises(ValueError):data.validate_rows(self.rows(),30,[{}],LIMITS,data.DIRECTIONS)
    def test_observed_timing_and_official_soft_clip(self):
        rows=self.rows();rows[0]['action'][-1]=100.
        timing=[{'observation_start_s':i*.04} for i in range(4)]
        a,intervals,source,clipped=data.validate_rows(rows,30,timing,LIMITS,data.DIRECTIONS)
        self.assertEqual(a[0]['motor']['gripper'],270);self.assertEqual(a[0]['raw']['gripper.pos'],100)
        self.assertEqual(clipped,1);self.assertAlmostEqual(intervals[0],.04);self.assertEqual(source,'采集实测时间')
    def execute(self,mode='normal',move=None):
        folder=self.jobs/('a'*32);folder.mkdir();(folder/'heartbeat').touch();(self.jobs/'current').write_text(folder.name)
        robot=Robot(folder,mode);timer=Timer()
        actions,intervals,_,_=data.validate_rows(self.rows(),30,None,LIMITS,data.DIRECTIONS)
        robot.pos=actions[0]['motor'].copy()
        request={'id':folder.name,'mode':'replay','dataset':'raw','episode':0,'root':str(self.root)}
        result=worker.run(folder,request,robot,actions,intervals,clock=timer.clock,sleep=timer.sleep,move=move or (lambda *args:None))
        return result,robot,timer
    def test_full_completion_returns_before_disable_and_pending_human(self):
        result,robot,timer=self.execute()
        self.assertEqual(result['state'],'completed');self.assertTrue(result['return_completed']);self.assertEqual(result['acceptance'],'pending')
        self.assertEqual(result['frames'],4);self.assertEqual(len(robot.sent),4)
        self.assertLess(robot.events.index('return'),robot.events.index('disable'))
        self.assertAlmostEqual(timer.t,4/30);self.assertEqual(robot.sent[0]['gripper.pos'],7)
    def test_manual_end_returns_without_acceptance(self):
        result,robot,_=self.execute('manual')
        self.assertEqual(result['state'],'stopped');self.assertTrue(result['return_completed']);self.assertEqual(result['frames'],2)
        self.assertLess(robot.events.index('return'),robot.events.index('disable'))
    def test_cancel_during_start_pose_returns(self):
        def stop(robot,targets,check,*args):(robot.folder/'stop').touch();check()
        result,robot,_=self.execute(move=stop)
        self.assertEqual(result['state'],'stopped');self.assertTrue(result['return_completed']);self.assertEqual(robot.sent,[])
    def test_hardware_and_partial_connect_faults_disable_without_blind_return(self):
        for mode in ('send_error','connect_error'):
            with self.subTest(mode=mode):
                folder=self.jobs/('a'*32)
                if folder.exists():import shutil;shutil.rmtree(folder)
                result,robot,_=self.execute(mode)
                self.assertEqual(result['state'],'error');self.assertFalse(result['return_completed']);self.assertNotIn('return',robot.events);self.assertIn('disable',robot.events)
    def test_return_error_is_never_reported_as_success(self):
        result,robot,_=self.execute('return_error');self.assertEqual(result['state'],'error');self.assertFalse(result['return_completed']);self.assertIn('disable',robot.events)
    def test_start_pose_is_slow_and_verified(self):
        folder=self.root/'pose';folder.mkdir();robot=Robot(folder);timer=Timer();target={n:20. for n in data.NAMES}
        worker.move_to_start(robot,target,lambda:None,lambda *args:None,timer.clock,timer.sleep)
        self.assertGreaterEqual(timer.t,3.4);self.assertLess(timer.t,4.)
        self.assertEqual(robot.pos,target);self.assertEqual(robot.sent[-1]['gripper.pos'],20/6)
        steps=[a['shoulder_pan.pos'] for a in robot.sent];self.assertLess(max(b-a for a,b in zip(steps,steps[1:])),20/50)
    def test_lost_heartbeat_is_error_not_pass(self):
        def stale(robot,*args):import os;os.utime(robot.folder/'heartbeat',(1,1))
        result,robot,_=self.execute(move=stale)
        self.assertEqual(result['state'],'error');self.assertNotIn('return',robot.events)
    def test_no_catchup_bursts(self):
        result,robot,timer=self.execute()
        self.assertTrue(all(0<v<=.02 for v in timer.sleeps));self.assertEqual(len(robot.sent),4)
    def test_acceptance_requires_latest_full_returned_unchanged_attempt(self):
        group=library.create('test','task');library.add_members(group['id'],'raw',[{'index':0,'frames':4}])
        result,_,_=self.execute();ident=result['id'];library.begin_replay(ident,'raw',0,'source')
        request={'id':ident,'dataset':'raw','episode':0,'verdict':'passed','note':'success'};raw=self.root/'raw';raw.mkdir()
        with patch.object(replay,'source_identity',return_value='source'):
            replay.review(raw,request)
            self.assertEqual(library.snapshot()['members'][0]['review'],'keep')
            self.assertEqual(replay.summaries()[('raw',0)]['status'],'passed')
            recording.write_json(self.jobs/ident/'status.json',{**result,'state':'stopped'})
            with self.assertRaisesRegex(ValueError,'完整回放'):replay.review(raw,request)
            recording.write_json(self.jobs/ident/'status.json',result)
            with patch.object(replay,'source_identity',return_value='changed'),self.assertRaisesRegex(ValueError,'源文件'):replay.review(raw,request)
            library.begin_replay('b'*32,'raw',0,'source')
            with self.assertRaisesRegex(ValueError,'较新的'):replay.review(raw,request)
    def test_device_lock_and_missing_preparation_never_spawn(self):
        plan={'digest':'sha','legacy':False}
        with patch.object(replay,'plan',return_value=plan),patch.object(can_control:=replay.can_control,'read_state',return_value={'up':True,'bitrate':1000000,'bus_state':'ERROR-ACTIVE'}),patch.object(replay.subprocess,'Popen') as spawn:
            with self.assertRaises(ValueError):replay.start(self.root,{'episode':0}, {})
            with (self.jobs/'device.lock').open('a') as lock:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                with self.assertRaisesRegex(ValueError,'设备被占用'):replay.start(self.root,{'episode':0,'prepared':True,'digest':'sha'}, {})
            spawn.assert_not_called()
    def test_stale_preview_and_unconfirmed_history_never_spawn(self):
        with patch.object(replay,'plan',return_value={'digest':'new','legacy':True}),patch.object(replay.subprocess,'Popen') as spawn:
            with self.assertRaisesRegex(ValueError,'重新预览'):replay.start(self.root,{'episode':0,'prepared':True,'digest':'old'}, {})
            with self.assertRaisesRegex(ValueError,'历史示范'):replay.start(self.root,{'episode':0,'prepared':True,'digest':'new'}, {})
            spawn.assert_not_called()

    def test_http_auth_validation_and_plan_dispatch(self):
        import threading
        from urllib.request import Request,urlopen
        from urllib.error import HTTPError
        server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        origin=f'http://127.0.0.1:{server.server_port}'
        headers={'Origin':origin,'X-Workbench-Token':app.CSRF_TOKEN}
        try:
            with patch.object(app,'replay_action',return_value={'frames':4}) as action:
                with self.assertRaises(HTTPError) as error:urlopen(Request(origin+'/api/replay/start',data=b'{}',method='POST'))
                self.assertEqual(error.exception.code,403);action.assert_not_called()
                body={'dataset':'raw','episode':0}
                with urlopen(Request(origin+'/api/replay/plan',data=json.dumps(body).encode(),headers=headers,method='POST')) as response:self.assertEqual(json.load(response)['frames'],4)
                action.assert_called_once_with('plan',body)
        finally:server.shutdown();server.server_close();thread.join()

    def test_plan_cannot_bypass_deletion_or_override_source(self):
        with patch.object(app.library,'snapshot',return_value={'members':[{'dataset':'raw','episode':0,'trashed':1}]}),patch.object(app.replay,'plan') as preflight:
            with self.assertRaisesRegex(ValueError,'已删除'):app.replay_action('plan',{'dataset':'raw','episode':0})
            preflight.assert_not_called()
        for body in ({'dataset':'raw','episode':True},{'dataset':'raw','episode':0,'root':'/etc'},{'dataset':'raw','episode':0,'prepared':True}):
            with self.assertRaises(ValueError):app.replay_action('plan',body)

    def test_invalid_target_or_failed_status_write_still_disables(self):
        def bad_position(robot,*_):robot.pos['shoulder_pan']=-100
        result,robot,_=self.execute(move=bad_position)
        self.assertEqual(result['state'],'error');self.assertEqual(robot.sent,[]);self.assertIn('disable',robot.events)
        import shutil;shutil.rmtree(self.jobs/('a'*32))
        original=worker.write_json
        def fail_return(path,state):
            if state['state']=='returning':raise OSError('disk full')
            original(path,state)
        with patch.object(worker,'write_json',side_effect=fail_return):result,robot,_=self.execute()
        self.assertEqual(result['state'],'error');self.assertIn('return',robot.events);self.assertIn('disable',robot.events)


if __name__=='__main__':unittest.main()
