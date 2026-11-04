"""Prompt-layer API: read effective P0-P6 text, save operator edits, reset.

Edits change wording only. Rendering and prompt submission remain server-side.
"""
from __future__ import annotations

from fastapi import Depends

from trading_system.server.errors import ApiError

from .prompt_templates import PromptTemplateError, PromptTemplateService
from .prompting import KNOWN_PROMPT_VARIABLES


def install_prompt_routes(app, prompts: PromptTemplateService, authenticated, mutation):
    def _payload(layer) -> dict:
        return {"layer": layer.as_payload(),
                "layers": [item.as_payload() for item in prompts.layers()],
                "framework": prompts.audit_payload(),
                "available_variables": sorted(KNOWN_PROMPT_VARIABLES),
                "max_length": 12000}

    def _error(exc: PromptTemplateError):
        return ApiError(exc.code, exc.message, 409 if exc.code == "PROMPT_TEMPLATE_CONFLICT" else 422)

    @app.get("/api/prompt-layers")
    def list_prompt_layers(_=Depends(authenticated)):
        return {"layers": [layer.as_payload() for layer in prompts.layers()],
                "framework": prompts.audit_payload(),
                "available_variables": sorted(KNOWN_PROMPT_VARIABLES),
                "max_length": 12000}

    @app.put("/api/prompt-layers/{layer_id}")
    def save_prompt_layer(layer_id: str, payload: dict, _=Depends(mutation)):
        try:
            layer = prompts.save(layer_id, payload.get("template"),
                                 expected_version=payload.get("expected_version"))
        except PromptTemplateError as exc:
            raise _error(exc) from None
        return _payload(layer)

    @app.post("/api/prompt-layers/{layer_id}/reset")
    def reset_prompt_layer(layer_id: str, payload: dict | None = None, _=Depends(mutation)):
        expected = (payload or {}).get("expected_version")
        try:
            current = prompts.layer(layer_id)
            if expected is not None and expected != current.version:
                raise PromptTemplateError("PROMPT_TEMPLATE_CONFLICT",
                                          "该层提示词已被其他页面修改，请刷新后重试。")
            layer = prompts.reset(layer_id)
        except PromptTemplateError as exc:
            raise _error(exc) from None
        return _payload(layer)

    @app.get("/api/prompt-layers/{layer_id}/history")
    def prompt_layer_history(layer_id: str, _=Depends(authenticated)):
        try:
            return {"layer_id": layer_id.upper(), "versions": list(prompts.history(layer_id))}
        except PromptTemplateError as exc:
            raise _error(exc) from None

    @app.post("/api/prompt-layers/preview")
    def preview_prompt_layer(payload: dict, _=Depends(mutation)):
        try:
            return prompts.preview(payload.get("template"), layer_id=payload.get("layer_id"))
        except PromptTemplateError as exc:
            raise _error(exc) from None
