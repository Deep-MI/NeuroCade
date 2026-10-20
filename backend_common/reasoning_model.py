"""Preserve reasoning fields supplied by OpenAI-compatible chat APIs."""

from typing import Any

from langchain_openai import ChatOpenAI


class ReasoningChatOpenAI(ChatOpenAI):
    """Keep vendor reasoning alongside content, without changing tool parsing."""

    def _convert_chunk_to_generation_chunk(
        self, chunk: dict, default_chunk_class: type, base_generation_info: dict | None,
    ):
        result = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info,
        )
        choices = chunk.get("choices") or chunk.get("chunk", {}).get("choices", [])
        if result is not None and choices:
            self._preserve_reasoning(result.message, choices[0].get("delta") or {})
        return result

    def _create_chat_result(self, response: Any, generation_info: dict | None = None):
        result = super()._create_chat_result(response, generation_info)
        data = response if isinstance(response, dict) else response.model_dump()
        for generation, choice in zip(result.generations, data.get("choices", []), strict=True):
            self._preserve_reasoning(generation.message, choice.get("message") or {})
        return result

    @staticmethod
    def _preserve_reasoning(message: Any, data: dict) -> None:
        reasoning = data.get("reasoning_content") or data.get("reasoning")
        if isinstance(reasoning, str) and reasoning:
            message.additional_kwargs["reasoning_content"] = reasoning
