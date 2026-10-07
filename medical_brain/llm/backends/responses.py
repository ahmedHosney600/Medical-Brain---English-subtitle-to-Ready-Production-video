"""OpenAI Responses API: OpenCode Go's Grok, GPT Luna and Muse Spark models."""
from ..config import thinking_mode
from ..errors import _thinking_refused
from ..progress import _Progress, _step_timeout
from .openai_compat import OpenAICompatibleBackend


class OpenAIResponsesBackend(OpenAICompatibleBackend):
    """OpenAI Responses API (/responses): OpenCode Go's Grok, GPT Luna and Muse Spark models."""

    def _once(self, system_prompt, user_prompt, temperature, limit) -> tuple:
        import openai
        params = dict(model=self.model, instructions=system_prompt, input=user_prompt, stream=True)
        if limit is not None:
            params["max_output_tokens"] = limit
        if temperature is not None and not self.no_temperature:
            params["temperature"] = temperature
        if thinking_mode(self.cfg, self.model) and not getattr(self, "no_thinking", False):
            params["reasoning"] = {"effort": "low"}   # the Responses API has no "off"
        progress = _Progress(self.model, _step_timeout(self.cfg))

        def create():
            try:
                return self.client.responses.create(**params)
            except openai.BadRequestError as e:
                if "temperature" in params and "temperature" in str(e).lower():
                    self.no_temperature = True
                    params.pop("temperature")
                    return self.client.responses.create(**params)
                if "reasoning" in params and _thinking_refused(e):
                    self.no_thinking = True
                    params.pop("reasoning")
                    return self.client.responses.create(**params)
                raise

        def run():
            stream = create()
            parts, incomplete, final_text = [], False, None
            try:
                for event in stream:
                    kind = getattr(event, "type", "")
                    if kind == "response.output_text.delta":
                        parts.append(event.delta)
                        progress.tick(writing=1)
                    elif "reasoning" in kind and kind.endswith(".delta"):
                        progress.tick(thinking=1)
                    elif kind == "response.incomplete":
                        incomplete = True
                        progress.tick()
                    elif kind == "response.completed":
                        final_text = getattr(event.response, "output_text", None)
                        progress.tick()
                    else:
                        progress.tick()
            finally:
                try:
                    stream.close()
                except Exception:
                    pass
                progress.done()
            return (final_text if final_text is not None else "".join(parts)), incomplete
        return self._map_errors(run)
