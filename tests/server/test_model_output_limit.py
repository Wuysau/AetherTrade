"""No application token cap means omit optional provider request fields."""
import pytest
from server.test_model_profiles import service, profile_payload
from trading_system.server import model_client
from trading_system.server.errors import ApiError

@pytest.mark.parametrize('protocol',['openai_responses','openai_chat'])
def test_unlimited_config_and_payload_omit_token_parameter(service,protocol):
    config=service.save_config(dict(profile_payload(),expected_revision=0,max_output_tokens=None,protocol=protocol))
    assert config['max_output_tokens'] is None
    _,body=model_client.payload(config,{'symbol':'BTCUSDT'})
    assert not {'max_output_tokens','max_completion_tokens','max_tokens'} & body.keys()


def test_anthropic_requires_explicit_protocol_limit(service):
    with pytest.raises(ApiError):
        service.save_config(dict(profile_payload(),expected_revision=0,max_output_tokens=None,protocol='anthropic'))


def test_numeric_limit_remains_supported(service):
    config=service.save_config(dict(profile_payload(),expected_revision=0,max_output_tokens=4096))
    _,body=model_client.payload(config,{'symbol':'BTCUSDT'})
    assert body['max_tokens']==4096
