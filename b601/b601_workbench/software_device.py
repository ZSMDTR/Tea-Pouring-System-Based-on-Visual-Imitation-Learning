"""Generate clearly labeled software-only data to exercise the UI without hardware."""
import json
from pathlib import Path
import sys


def generate(request):
    import numpy as np
    from lerobot.datasets.lerobot_dataset import LeRobotDataset
    from experiment_data import JOINTS
    root=Path(request['output'])
    if root.exists():raise ValueError('输出目录已存在。')
    features={'action':{'dtype':'float32','shape':(7,),'names':JOINTS},
              'observation.state':{'dtype':'float32','shape':(7,),'names':JOINTS},
              'observation.images.wrist_rgb':{'dtype':'video','shape':(480,640,3),'names':['height','width','channels']}}
    dataset=LeRobotDataset.create('local/'+root.name,30,root=root,robot_type='software_device',features=features,use_videos=True,image_writer_threads=2,vcodec='h264')
    (root/'workbench_simulation.json').write_text(json.dumps({'software_only':True,'project':request.get('project'),'frames':60,'note':'Synthetic UI test data; no dynamics, motor timing or task success is simulated.'}))
    try:
        for frame in range(60):
            image=np.zeros((480,640,3),dtype=np.uint8);image[:]=[28,58,49]
            x=30+frame*8;image[180:280,x:min(x+55,640)]=[221,169,78]
            action=np.asarray([np.sin(frame/12+i)*5 for i in range(7)],dtype=np.float32)
            dataset.add_frame({'action':action,'observation.state':action.copy(),'observation.images.wrist_rgb':image,'task':'Software device workflow test; no physical motion.'})
        dataset.save_episode();dataset.finalize()
    finally:
        dataset.stop_image_writer()
    return {'dataset_output':root.name}


if __name__=='__main__':generate(json.loads(Path(sys.argv[1]).read_text()))
