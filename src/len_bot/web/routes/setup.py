import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError

from len_bot.config_edit import ConfigEditConflict
from len_bot.web import group_quick
from len_bot.web.auth import get_current_user
from len_bot.tools.results import error_message

logger = logging.getLogger(__name__)

router = APIRouter(prefix='/api/setup', tags=['setup'])


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
    except group_quick.GroupSettingsApplyError as error:
        raise HTTPException(409, {'message': str(error), 'scene_id': req.scene_id,
                                  'config_saved': True, 'stage': 'apply'}) from error
    except ConfigEditConflict:
        raise
    except ValidationError as error:
        raise HTTPException(422, error.errors(include_input=False, include_context=False)) from error
    except ValueError as error:
        raise HTTPException(400, {'message': str(error), 'scene_id': req.scene_id,
                                  'config_saved': False}) from error
    except OSError as error:
        raise HTTPException(500, '本群配置写入未取得完成确认，请核对根配置：' + str(error.strerror)) from error
    try:
        joined = await runtime.query_service.joined_groups()
        record = await group_quick.assemble(runtime.query_service, req.scene_id, joined=joined)
    except Exception as error:
        detail = '本群设置已保存并完成运行应用，但保存值读取失败：' + error_message(f'{type(error).__name__}: {error}')
        logger.error('%s [%s]', detail, req.scene_id)
        raise HTTPException(409, {'message': detail, 'scene_id': req.scene_id,
                                  'config_saved': True, 'stage': 'readback'}) from error
    return {**record, 'message': '本群设置已保存，后续输入与发送使用当前规则',
            'config_saved': True, 'requires_restart': runtime.restart_required}
