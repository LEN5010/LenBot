"""Account task admission and ordinary-task scope with synthetic identities."""
import asyncio
import json
from pathlib import Path

import pytest

from len_bot.next.config import HostConfig
from len_bot.next.storage.store import Store
from len_bot.next.work.service import WorkTasks
from len_bot.next.work.tools import execute_tasks


def test_account_task_requires_root_owner_and_new_workspace(tmp_path: Path):
    source = {
        'mode':'isolated-multi','bot_qq':'90001','owner_qq':'70001','timezone':'UTC',
        'permissions':{'admins':['70003'],'blacklist':['70004']},
        'database':str(tmp_path/'state.db'),'onebot':{'mode':'forward_ws','ws_url':'ws://127.0.0.1:9'},
        'models':{'providers':{'fixture':{'api':'openai-chat','base_url':'http://127.0.0.1:9/v1','api_key':'fixture'}},
                  'roles':{role:{'provider':'fixture','model':'fixture','context_window_tokens':65536} for role in ['mind','voice','worker']}},
        'scenes':{'group:80001':{'persona':str(tmp_path/'persona'),'permissions':{'whitelist':['70005']},'tasks':{'enabled':True,'owner':'70002'}}},
        'worker':{'docker_binary':'/usr/bin/false','docker_host':'unix:///private/tmp/unused.sock','image':'fixture',
                  'workspace_root':str(tmp_path/'work'),'runtime_root':str(tmp_path/'run'),'delivery_root':str(tmp_path/'out'),
                  'active_timeout_seconds':3600,'uid':10000,'gid':10000,'model_reasoning':False},
        'account_browser':{'socket':'/private/tmp/unused-browser.sock','browser_instance_id':'fixture',
                           'binary':'/usr/bin/false','home':str(tmp_path/'browser')},
    }
    cfg = HostConfig.model_validate_json(json.dumps(source))
    async def exercise():
        with Store(cfg.database) as store:
            service = WorkTasks(cfg,store,None,lambda scene:None,skills={'group:80001':()},
                                memory=None,data_tools={'group:80001':[]},skill_permissions={'group:80001':[]},
                                tool_permissions={'group:80001':[]})
            service.accepting = True  # Admission only, no task pump or container is started.
            kwargs = {'goal':'合成网页任务','deliverable':'原文','context':'主人明确要求独立账号任务','account_browser':True}
            with pytest.raises(PermissionError,match='根配置的主人'):
                await service.delegate('group:80001',requester='70002',**kwargs)
            task = await service.delegate('group:80001',requester='70001',**kwargs)
            assert task['account_browser'] and not task['browser_active']
            assert task['active_timeout_seconds'] == 3600
            ordinary = {**kwargs, 'account_browser':False}
            admin = await service.delegate('group:80001',requester='70003',**ordinary)
            assert admin['active_timeout_seconds'] == 1800
            out = cfg.worker.workspace_root / 'group:80001' / 'tasks' / str(admin['id']) / 'out'
            out.mkdir(parents=True)
            (out / 'schedule.csv').write_text('活动,人数\n读书会,12\n')
            (out / 'nested').mkdir()
            (out / 'nested' / 'draft.html').write_text('<p>实际草稿</p>')
            outside = tmp_path / 'private-files'
            outside.mkdir()
            (outside / 'secret.txt').write_text('outside task')
            (out / 'linked').symlink_to(outside, target_is_directory=True)
            args = {'action':'outputs', 'id':admin['id'], 'requester':'70003'}
            outputs = await execute_tasks(service, 'group:80001', 'task', args)
            assert outputs['registered_files'] == []
            assert [(e['path'], e['kind']) for e in outputs['entries']] == [
                ('linked', 'symlink'), ('nested', 'directory'), ('schedule.csv', 'file')]
            nested = await execute_tasks(service, 'group:80001', 'task', {**args, 'path':'nested'})
            assert nested['entries'][0]['path'] == 'nested/draft.html'
            with pytest.raises(PermissionError):
                await execute_tasks(service, 'group:80001', 'task', {**args, 'requester':'70006'})
            with pytest.raises(PermissionError, match='根主人'):
                await execute_tasks(service, 'group:80001', 'task', {**args, 'id':task['id']})
            with pytest.raises(OSError):
                await execute_tasks(service, 'group:80001', 'task', {**args, 'path':'linked'})
            with pytest.raises(ValueError, match='relative directory'):
                await execute_tasks(service, 'group:80001', 'task', {**args, 'path':'../private-files'})
            white = await service.delegate('group:80001',requester='70005',**ordinary)
            assert white['active_timeout_seconds'] == 3600
            with pytest.raises(PermissionError, match='黑名单'):
                await service.delegate('group:80001',requester='70004',**ordinary)
            previous = service.records.create('group:80001','70004','以前的任务','原交付','','原输入')
            assert (await service.cancel('group:80001',previous.id,requester='70004'))['status'] == 'cancelled'

            service.records.finish('group:80001',task['id'],'failed',None,'fixture stop')
            with pytest.raises(ValueError,match='不续接旧工作区'):
                await service.resume('group:80001',task['id'],requester='70001',text='继续')
            service.records.browser_binding('group:80001',task['id'],active=True,session='actual-native-session')
            with pytest.raises(ValueError,match='仍被任务占用'):
                await service.delegate('group:80001',requester='70001',**kwargs)
    asyncio.run(exercise())
