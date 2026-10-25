"""Fixed experiment commands, no shell and no dataset overwrite."""
import json
import os
from pathlib import Path
import subprocess
import sys
import traceback
import signal
import time
from recording import write_json
import experiment_data
import checkpoint_catalog

ROOT=Path(__file__).resolve().parent
OPENPI=ROOT.parent/'openpi'


class Cancelled(Exception):
    pass


def run_child(args,folder,lock_fd,env=None):
    if (folder/'cancel').exists():raise Cancelled()
    with subprocess.Popen(args,cwd=OPENPI,env=env,pass_fds=(lock_fd,),start_new_session=True) as child:
        sent=False
        while child.poll() is None:
            if (folder/'cancel').exists() and not sent:
                try:phase=json.loads((folder/'progress.json').read_text()).get('phase','')
                except (OSError,ValueError):phase=''
                if phase!='串行保存中':
                    # Signal only the child group created by this worker.
                    os.killpg(child.pid,signal.SIGINT);sent=True
            time.sleep(.2)
        if sent:raise Cancelled()
        if child.returncode:raise subprocess.CalledProcessError(child.returncode,args)
    if (folder/'cancel').exists():raise Cancelled()

def main(folder, lock_fd):
    request=json.loads((folder/'request.json').read_text());config=request['config']
    status={**request,'config':None}
    def update(state,message):status.update(state=state,message=message);write_json(folder/'status.json',status)
    def run(args,env=None):return run_child(args,folder,lock_fd,env)
    try:
        source=Path(request['source']);target=Path(request['output'])
        if target.exists():raise ValueError('输出目录已存在，拒绝覆盖。')
        if request['kind']=='simulation':
            update('generating','正在生成软件设备示例数据；不会打开相机或连接电机。')
            run([config['lerobot_python'],'-B',str(ROOT/'software_device.py'),str(folder/'request.json')])
            status['dataset_output']=target.name
            update('completed','软件设备示例已生成，可预览、检查和整理；不能用于实机回放或模型训练。')
        elif request['kind']=='audit':
            update('checking','正在逐条检查数据、视频文件和采集信息…')
            run([config['lerobot_python'],'-B',str(ROOT/'quality_audit.py'),str(folder)])
            report=json.loads((folder/'quality_report.json').read_text());status['quality_summary']=report['summary'];status['output']=str(folder/'quality_report.json')
            update('completed','数据检查完成；请在质量报告中人工确认候选问题。')
        elif request['kind'] in ('import_hf','upload_hf'):
            update('transferring','正在下载 Hugging Face 数据集…' if request['kind']=='import_hf' else '正在上传数据集到指定 Hugging Face 仓库…')
            run([config['lerobot_python'],'-B',str(ROOT/'exchange_worker.py'),str(folder)])
            result=json.loads((folder/'exchange_result.json').read_text());status.update(result)
            update('completed',result['message'])
        elif request['kind']=='export':
            update('exporting','正在按保留清单导出示范并转换视频…')
            staging=Path(config['dataset_root'])/('.preparing_'+request['id'])
            run([config['lerobot_python'],'-B',str(ROOT/'export_worker.py'),str(folder/'request.json'),str(staging)])
            update('validating','正在使用 OpenPI 环境检查每条示范首尾帧及动作片段…')
            env=os.environ.copy();env.update(JAX_PLATFORMS='cpu',CUDA_VISIBLE_DEVICES='',HF_HUB_OFFLINE='1')
            run([config['openpi_python'],'-B',str(ROOT/'verify_export.py'),str(staging)],env)
            meta=json.loads((staging/'workbench_export.json').read_text());meta['openpi_reader']='passed'
            write_json(staging/'workbench_export.json',meta)
            staging.rename(target);status['dataset_output']=target.name
            update('completed',f"导出完成：{len(request['export_plan']['items'])} 条保留示范。OpenPI 读取检查通过；可选择该导出结果训练，Replay 状态仍待验收。")
        elif request['kind']=='convert':
            update('converting','正在转换视频与元信息；原始数据保持不变。')
            staging=Path(config['dataset_root'])/('.preparing_'+request['id'])
            info=json.loads((source/'meta/info.json').read_text())
            cameras=[key for key,value in info['features'].items() if value.get('dtype')=='video' and 'depth' not in key]
            keys=[item for key in cameras for item in ('--video-key',key)]
            run([config['lerobot_python'],str(OPENPI/'scripts/b601/convert_dataset.py'),'--src',str(source),'--dst',str(staging),'--video-mode','reencode-h264',*keys])
            report=experiment_data.inspect(staging)
            if not report['compatible'] or report['needs_conversion']:raise ValueError('转换后的格式检查未通过。')
            write_json(staging/'workbench_conversion.json',{'source':str(source),'source_report':request['report'],'accepted_by_replay':False})
            staging.rename(target)
            status['dataset_output']=target.name
            update('completed','转换完成。已生成独立 v2.1 副本；Replay 验收状态未改变。')
        else:
            full=request.get('save_mode')=='full'
            python=str(checkpoint_catalog.FULL_PYTHON) if full else config['openpi_python']
            entry=str(ROOT/'full_training/entry.py') if full else str(ROOT/'training_runner.py')
            import camera_contract
            inputs=camera_contract.validate(request.get('camera_contract',camera_contract.contract()))
            if inputs['mode']==camera_contract.DUAL:
                entry=str(ROOT/'multi_view_training.py')
            # Persist before training can create a usable checkpoint. Even if
            # the supervisor is interrupted, inference cannot mistake dual for legacy.
            write_json(camera_contract.sidecar(target),inputs)
            update('normalizing','正在准备本次数据的归一化统计…' if not request.get('resume_from') else '正在读取存档的归一化统计…')
            env=os.environ.copy();env.update(JAX_PLATFORMS='cpu',CUDA_VISIBLE_DEVICES='',HF_HUB_OFFLINE='1')
            run([python,'-B',entry,'norm',str(folder)],env)
            update('training','正在训练：定期串行保存完整状态，可从已完成存档恢复。' if full else '正在训练。当前模式只在正常结束时保存推理参数，不支持断点续训。')
            env=os.environ.copy();env.update(HF_HUB_OFFLINE='1',XLA_PYTHON_CLIENT_MEM_FRACTION='0.8',WANDB_MODE='disabled')
            if full:
                env.update(XLA_PYTHON_CLIENT_ALLOCATOR='platform',XLA_PYTHON_CLIENT_PREALLOCATE='false',
                    XLA_FLAGS='--xla_gpu_exclude_nondeterministic_ops=true --xla_gpu_autotune_level=0',CUBLAS_WORKSPACE_CONFIG=':4096:8')
            run([python,'-B',entry,'train',str(folder)],env)
            if full:
                final=target/str(request['steps'])
                checkpoint_catalog.read(final)
                update('completed','训练完成，完整状态和推理参数均已保存；可在完整存档列表中继续训练。')
            else:
                final=target/str(request['steps']-1)
                if not (final/'params').is_dir() or not (final/'FINAL_PARAMS_ONLY.txt').is_file():raise ValueError('训练进程结束，但未找到完整的最终参数标记。')
                write_json(final/'camera_contract.json',inputs)
                update('completed','训练完成，最终推理参数已保存；此结果不能精确断点续训。')
    except Cancelled:
        update('cancelled','已停止。已完成的存档和原始数据保留；本次未完成输出不可用于训练或部署。')
    except BaseException as exc:
        traceback.print_exc();update('error',str(exc) or type(exc).__name__)

if __name__=='__main__':main(Path(sys.argv[1]),int(sys.argv[2]))
