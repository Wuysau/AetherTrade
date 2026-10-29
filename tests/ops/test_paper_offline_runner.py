"""An offline dependency image changes setup only, never isolation or test gates."""
import pytest


def test_offline_runner_requires_an_immutable_image_id():
    from tools.qualify_paper_docker import dependency_setup
    for image in ('python:3.12-slim','local-deps:latest','',None):
        with pytest.raises(ValueError,match='immutable'):
            dependency_setup(image,offline=True)


def test_offline_setup_installs_exact_checked_out_source_without_resolving_network_dependencies():
    from tools.qualify_paper_docker import dependency_setup
    image='sha256:'+'a'*64
    linux,python=dependency_setup(image,offline=True)
    assert linux is None
    assert python==['python','-m','pip','install','--no-deps','--no-build-isolation','-e','.']
    online_linux,online_python=dependency_setup('python:3.12-slim',offline=False)
    assert online_linux and online_python==['python','-m','pip','install','-e','.[test]']
