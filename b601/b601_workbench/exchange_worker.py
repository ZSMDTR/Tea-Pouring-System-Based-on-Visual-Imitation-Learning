"""HF transfers run only after an explicit job request, never at page load."""
import json
from pathlib import Path
import shutil
import sys
from recording import write_json


def run(request,folder):
    from huggingface_hub import HfApi, snapshot_download
    api=HfApi();repo=request['repo'];config=request['config']
    if request['kind']=='import_hf':
        info=api.repo_info(repo,repo_type='dataset',revision=request.get('revision','main'),files_metadata=True)
        commit=info.sha
        size=sum(f.size or 0 for f in info.siblings)
        base=Path(config['dataset_root']);target=Path(request['output']);staging=base/('.preparing_'+request['id'])
        if target.exists() or staging.exists():raise ValueError('输出目录已存在。')
        if shutil.disk_usage(base).free < size+2*2**30:raise ValueError('空间不足以下载数据集并保留 2 GiB。')
        snapshot_download(repo_id=repo,repo_type='dataset',revision=commit,local_dir=str(staging))
        if any(p.is_symlink() for p in staging.rglob('*')):raise ValueError('下载结果包含符号链接，未纳入数据目录。')
        meta=staging/'meta/info.json'
        if not meta.is_file():raise ValueError('仓库中没有 LeRobot meta/info.json。')
        manifest=json.loads(meta.read_text())
        if manifest.get('codebase_version') not in ('v2.1','v3.0'):raise ValueError('目前支持导入 LeRobot v2.1 / v3.0。')
        write_json(staging/'workbench_import.json',{'repo':repo,'revision':commit,'imported_by':'b601_workbench','project':request.get('project')})
        staging.rename(target)
        return {'dataset_output':target.name,'message':'下载完成，已固定仓库版本；请检查格式与任务内容后再训练。'}
    source=Path(request['source']).resolve()
    if source.parent!=Path(config['dataset_root']).resolve():raise ValueError('本地数据目录无效。')
    from dataset_export import inventory
    before=inventory(source)
    # Existing repository visibility is checked instead of silently changing it.
    try:
        remote=api.repo_info(repo,repo_type='dataset')
        if bool(remote.private)!=bool(request['private']):raise ValueError('现有仓库可见性与所选选项不一致，请先核对仓库。')
    except Exception as exc:
        from huggingface_hub.errors import RepositoryNotFoundError
        if not isinstance(exc,RepositoryNotFoundError):raise
        api.create_repo(repo,repo_type='dataset',private=request['private'],exist_ok=False)
    api.upload_folder(repo_id=repo,repo_type='dataset',folder_path=str(source),commit_message='Upload dataset from B601 workbench',ignore_patterns=['.cache/**','**/*.tmp'])
    if inventory(source)!=before:raise ValueError('上传期间本地源数据发生变化，请核对远端提交。')
    return {'repo_url':'https://huggingface.co/datasets/'+repo,'message':'上传完成；远端仓库保留本次提交记录。'}


if __name__=='__main__':
    folder=Path(sys.argv[1]);request=json.loads((folder/'request.json').read_text())
    result=run(request,folder);write_json(folder/'exchange_result.json',result)
