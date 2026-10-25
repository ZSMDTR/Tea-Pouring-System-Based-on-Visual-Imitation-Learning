"""Validated Hugging Face transfer requests; credentials stay in the HF client."""
import re
from pathlib import Path


def options(kind,data,config,source=None):
    repo=data.get('repo','')
    if not isinstance(repo,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}/[A-Za-z0-9][A-Za-z0-9_.-]{0,95}',repo) or '..' in repo:
        raise ValueError('仓库格式应为 用户名/数据集名。')
    if data.get('acknowledged') is not True:raise ValueError('请确认本次下载或上传的对象。')
    revision=data.get('revision','main')
    if not isinstance(revision,str) or not 1<=len(revision)<=160:raise ValueError('仓库版本无效。')
    if kind=='upload_hf':
        if source is None or not (Path(source)/'meta/info.json').is_file():raise ValueError('请选择本地数据集。')
        if type(data.get('private',True)) is not bool:raise ValueError('仓库可见性无效。')
    return {'repo':repo,'private':data.get('private',True),'revision':data.get('revision','main')}
