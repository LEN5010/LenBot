from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError

from len_bot.config_edit import ConfigEditConflict
from len_bot.web import group_quick
from len_bot.web.auth import get_current_user

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
