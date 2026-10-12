from google import genai
from google.genai import types

from .prompts import SYSTEM
from .semantic_plan import validate_plan

PLAN_TOOL = types.FunctionDeclaration(
    name="submit_semantic_plan",
    description="Return the structured actions and expected assertions for this Jira step. No browser inspection or selectors.",
    parameters_json_schema={"type":"object", "properties":{
        "actions":{"type":"array", "minItems":1, "maxItems":10, "items":{"type":"object",
            "properties":{"type":{"type":"string", "enum":["click","fill","select","check","uncheck","press","navigate"]},
                          "target":{"type":"object", "properties":{key:{"type":"string"} for key in
                              ("name","roleHint","label","text","placeholder")}},
                          "value":{"type":"string"}, "key":{"type":"string"}, "url":{"type":"string"}},
            "required":["type"]}},
        "expectedAssertions":{"type":"array", "maxItems":10, "items":{"type":"object",
            "properties":{"type":{"type":"string", "enum":["visible","hidden","text","value","enabled",
                "disabled","checked","unchecked","selected","unselected","url"]},
                          "target":{"type":"object", "properties":{key:{"type":"string"} for key in
                              ("name","roleHint","label","text","placeholder")}},
                          "expected":{"type":"string"}}, "required":["type"]}}},
        "required":["actions","expectedAssertions"]})


class GeminiClient:
    def __init__(self, api_key: str, tools: list[dict], state, model: str):
        self.client = genai.Client(api_key=api_key, http_options=types.HttpOptions(
            timeout=90000, retry_options=types.HttpRetryOptions(attempts=1)))
        self.state = state
        self.model = model
        declarations = [types.FunctionDeclaration(name=t["name"], description=t.get("description", ""),
                                                  parameters_json_schema=t["inputSchema"]) for t in tools]
        self.config = types.GenerateContentConfig(
            system_instruction=SYSTEM, temperature=0, max_output_tokens=2048,
            tools=[types.Tool(function_declarations=declarations)],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            tool_config=types.ToolConfig(function_calling_config=types.FunctionCallingConfig(mode="ANY")))
        self.plan_config = types.GenerateContentConfig(
            system_instruction="""Translate exactly one Jira step into a semantic browser action plan.
Return only submit_semantic_plan. Do not call browser tools or request snapshots.
Use visible UI wording from the Jira action/expected result, never DOM IDs or selectors.
Use click/fill/select/check/uncheck/press/navigate and expectedAssertions.
Use credential environment variable names for login fields, never secret values.
Do not invent test values missing from Jira data; if intent cannot be planned, return an empty action list.
The executor will verify whether targets actually exist. Do not claim they exist.""",
            temperature=0, max_output_tokens=2048,
            tools=[types.Tool(function_declarations=[PLAN_TOOL])],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            tool_config=types.ToolConfig(function_calling_config=types.FunctionCallingConfig(mode="ANY")))

    async def plan_step(self, step, state, base_url, credential_names):
        import json
        from dataclasses import asdict
        request = {"currentStep":asdict(step), "browserState":state,
                   "baseUrl":base_url, "credentialNames":credential_names}
        self.state.metrics["geminiRequests"] += 1
        self.state.metrics["contextCharactersSent"] += len(json.dumps(request,ensure_ascii=False))
        response = await self.client.aio.models.generate_content(
            model=self.model, contents=json.dumps(request,ensure_ascii=False), config=self.plan_config)
        usage = response.usage_metadata
        if usage:
            for key, field in (("inputTokens", "prompt_token_count"), ("outputTokens", "candidates_token_count"),
                               ("thoughtTokens", "thoughts_token_count")):
                self.state.metrics[key] += getattr(usage,field,0) or 0
        parts = response.candidates[0].content.parts if response.candidates and response.candidates[0].content else []
        calls = [part.function_call for part in parts if part.function_call]
        if len(calls) != 1 or calls[0].name != "submit_semantic_plan":
            raise ValueError("Planner did not return a SemanticPlan")
        plan = dict(calls[0].args or {})
        validate_plan(plan,step.expectedResult)
        return plan

    async def request(self, contents):
        self.state.metrics["geminiRequests"] += 1
        self.state.metrics["contextCharactersSent"] += sum(len(c.model_dump_json()) for c in contents)
        response = await self.client.aio.models.generate_content(model=self.model, contents=contents, config=self.config)
        usage = response.usage_metadata
        if usage:
            for key, field in (("inputTokens", "prompt_token_count"), ("outputTokens", "candidates_token_count"),
                               ("thoughtTokens", "thoughts_token_count")):
                self.state.metrics[key] += getattr(usage, field, 0) or 0
        if not response.candidates or not response.candidates[0].content:
            raise RuntimeError("Gemini returned no executable content")
        return response.candidates[0].content

    async def close(self):
        await self.client.aio.aclose()
        self.client.close()
