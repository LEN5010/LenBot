from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from len_bot.config_edit import ConfigEditConflict
from len_bot.plugins.host import PluginConfigurationApplyError
from len_bot.web import group_quick, setup_wizards
from len_bot.web.auth import get_current_user

router = APIRouter(prefix='/api/setup', tags=['setup'])


class WizardRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    wizard: str
    values: dict = Field(default_factory=dict)


@router.get('/options')
async def setup_options(request: Request, user: str = Depends(get_current_user)):
    root = request.app.state.runtime.config_store.current
    members = [{'name': item.name, 'bilibili_uid': item.bilibili_uid, 'room_id': item.room_id}
               for item in root.members]
    scenes = [{'scene_id': scene_id} for scene_id in root.scenes]
    return {'wizards': list(setup_wizards.WIZARDS), 'broadcast_plugins': list(setup_wizards.BROADCAST_PLUGINS),
            'members': members, 'scenes': scenes, 'heartbeat_topics': list(root.runtime.heartbeat_topics),
            'heartbeat_enabled': root.runtime.heartbeat_enabled}


@router.post('/preview')
async def preview_wizard(req: WizardRequest, request: Request, user: str = Depends(get_current_user)):
    try:
        runtime = request.app.state.runtime
        return setup_wizards.preview(runtime.config_store.current, req.wizard, req.values, runtime.clock())
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.post('/apply')
async def apply_wizard(req: WizardRequest, request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    try:
        preview = await runtime.apply_setup_wizard(req.wizard, req.values, operator_id=user)
    except ConfigEditConflict:
        raise
    except PluginConfigurationApplyError as error:
        raise HTTPException(409, {'message': str(error), 'config_saved': True}) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    except OSError as error:
        raise HTTPException(500, '配置文件保存失败：' + str(error.strerror)) from error
    return {'success': True, 'preview': preview,
            'requires_restart': runtime.restart_required,
            'message': '向导改动已写入根配置；未改字段保持原值'}


@router.get('/group-quick')
async def group_quick_options(scene_id: str, request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    try:
        joined = await runtime.query_service.joined_groups()
        return await group_quick.assemble(runtime.query_service, scene_id, joined=joined)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@router.put('/group-quick')
async def apply_group_quick(req: group_quick.GroupQuickRequest, request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    try:
        await runtime.apply_group_quick(req.scene_id, req.baseline, req.values.model_dump(), operator_id=user)
    except ConfigEditConflict:
        raise
    except ValidationError as error:
        raise HTTPException(422, error.errors(include_input=False, include_context=False)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    except OSError as error:
        raise HTTPException(500, '配置文件保存失败，原群设置未发布：' + str(error.strerror)) from error
    joined = await runtime.query_service.joined_groups()
    record = await group_quick.assemble(runtime.query_service, req.scene_id, joined=joined)
    return {**record, 'message': '本群设置已保存，后续输入与发送使用当前规则',
            'requires_restart': runtime.restart_required}
