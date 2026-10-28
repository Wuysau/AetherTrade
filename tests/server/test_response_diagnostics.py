import pytest
from trading_system.server.model_client import parse
from trading_system.server.errors import ApiError


def response(text,status='completed'):
    return dict(status=status,output=[dict(type='message',content=[dict(type='output_text',text=text)])])

@pytest.mark.parametrize('body,stage',[
    (response('{}','incomplete'),'provider_completion'),
    (response('x'*16001),'output_size'),
    (response('```json\n{}\n```'),'json_format'),
    (response('{"action":"HOLD"}'),'schema_revision'),
])
def test_invalid_response_has_safe_precise_stage(body,stage):
    with pytest.raises(ApiError) as exc:
        parse({'protocol':'openai_responses'},body,'BTCUSDT',schema='assessment_v1')
    assert exc.value.response_diagnostic['stage']==stage
    assert 'text' not in exc.value.response_diagnostic
