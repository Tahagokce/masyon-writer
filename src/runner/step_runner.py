import asyncio
import json
from google.genai import types
from src.gemini.context import StepContext
from src.gemini.semantic_plan import validate_plan


async def run_fallback_step(step, ai, browser, state, config, secrets, log,
                            *, prestarted=False, semantic_result=None):
    context = StepContext(step, config, secrets)
    evidence, actions, errors = {}, int(bool(semantic_result and semantic_result.get("executed"))), False
    latest_mutation = int(bool(semantic_result and semantic_result.get("mutating")))
    sequence = int(bool(semantic_result))
    calls = 0
    no_progress = 0

    def failed(status, reason):
        return {"stepNumber": step.stepNumber, "status": status, "reason": reason,
                "verification": "", "evidence": list(evidence.values())}

    if semantic_result:
        context.resolver_failure = secrets.object({key:semantic_result.get(key) for key in
            ("resolverStatus", "reason", "candidates") if semantic_result.get(key) is not None})
        ids = semantic_result.get("evidenceIds") or []
        assertions = semantic_result.get("assertions") or []
        for index, item_id in enumerate(ids):
            evidence[item_id] = {"id":item_id,"sequence":sequence,"page":semantic_result.get("activePage"),
                                 "assertion":assertions[index] if index < len(assertions) else None}
    if not prestarted and any(tool["name"] == "poc_step_start" for tool in browser.tools):
        try:
            started = await browser.call("poc_step_start", {"stepNumber": step.stepNumber})
        except Exception as exc:
            return failed("BLOCKED", "Jira step page lock could not start: " + secrets.diagnostic(exc)["error"])
        if started.get("isError"):
            return failed("BLOCKED", "Jira step page lock could not start: " + started.get("error", "unknown error"))
        for event in started.pop("logEvents", []): log(event)
        state.observe(started)

    async def loop():
        nonlocal calls, sequence, actions, errors, latest_mutation, no_progress
        while calls < config.max_step_calls:
            content = await ai.request(context.request(state, evidence))
            functions = [part.function_call for part in content.parts or [] if part.function_call]
            if not functions:
                return failed("BLOCKED", "Gemini returned no tool calls")
            replies = []
            checkpoint_required = False
            for function in functions:
                calls += 1
                if calls > config.max_step_calls:
                    return failed("BLOCKED", "Step tool-call limit reached")
                name, args = function.name, dict(function.args or {})
                if checkpoint_required:
                    replies.append(types.Part(function_response=types.FunctionResponse(name=name, id=function.id,
                        response={"executed":False,"checkpointRequired":True,
                                  "reason":"Skipped after UI transition; inspect and replan before continuing."})))
                    continue
                if len(json.dumps(args)) > 8000:
                    return failed("BLOCKED", "Model tool arguments exceeded the context budget")
                if name == "finish_step":
                    status = args.get("status")
                    ids = args.get("evidenceIds", [])
                    valid_ids = isinstance(ids, list) and all(isinstance(i, str) and i in evidence for i in ids)
                    fresh = valid_ids and bool(ids) and all(evidence[i]["sequence"] >= latest_mutation for i in ids)
                    valid = status in {"COMPLETED", "FAILED", "BLOCKED"} and bool(args.get("reason"))
                    if status == "COMPLETED":
                        valid = valid and len(functions) == 1 and actions > 0 and not errors
                        if step.expectedResult:
                            valid = valid and fresh and bool(args.get("verification"))
                    if not valid:
                        result = {"isError": True, "reason": "Completion rejected: need successful operations and fresh expected-result assertion evidence; finish alone."}
                    else:
                        state.summary = secrets.clean(str(args.get("stateSummary", "")))[:400]
                        if status == "COMPLETED": state.completed_steps.append(step.stepNumber)
                        return secrets.object({"stepNumber": step.stepNumber, "status": status,
                            "reason": str(args["reason"])[:2000], "verification": str(args.get("verification", ""))[:2000],
                            "evidence": [evidence[i] for i in ids] if valid_ids else []})
                elif name == "generate_value":
                    try:
                        if secrets.contains(json.dumps(args)):
                            raise ValueError("Credentials cannot be registered as generated values")
                        value = state.generate(args.get("name", ""), args.get("prefix", "AUTO"), args.get("value"))
                        result = {"value": value}
                        log(f"[AI] Generated/reused value: {value}")
                    except ValueError as exc:
                        result = {"isError": True, "reason": str(exc)}
                elif name in {t["name"] for t in browser.tools if not t["name"].startswith("poc_")}:
                    sequence += 1
                    log(f"[AI] Requested tool: {name}")
                    try:
                        result = await browser.call(name, args)
                    except Exception as exc:
                        result = {"isError":True, **secrets.diagnostic(exc), "activePage":state.active_page}
                    for event in result.pop("logEvents", []): log(event)
                    state.metrics["browserToolCalls"] += result.get("batchExecuted", 1)
                    state.observe(result)
                    for notice in result.get("notices") or []: log(notice)
                    if (result.get("unchanged") or result.get("denied")) and not result.get("executed"):
                        no_progress += 1
                        log(f"[ORCHESTRATION] {result.get('reason', 'Browser state unchanged')}")
                        if no_progress >= 2:
                            return failed("BLOCKED", "Expected result not verified: " + (step.expectedResult or "current UI")[:240] +
                                          "; repeated unchanged snapshots or page switches without a browser state change")
                    elif result.get("mutating") or result.get("evidenceId"):
                        no_progress = 0
                    if result.get("mutating"):
                        latest_mutation = sequence
                    if result.get("isError"):
                        detail = secrets.object({"tool":result.get("failedTool",name), "errorClass":result.get("errorClass", "BrowserToolError"),
                            "message":result.get("error", "operation did not succeed"),
                            "activePage":state.active_page, "stepNumber":step.stepNumber})
                        log(f"[MCP] {json.dumps(detail, ensure_ascii=False)}")
                        # A failed assertion is a failed expected result; never "recover" by
                        # asserting a different, easier condition and claiming success.
                        if name == "browser_assert" and result.get("recoveryIncomplete"):
                            return {**failed("BLOCKED", f"Assertion target recovery incomplete: {detail['message']}"),
                                    "errorDetails":detail}
                        if not (name == "browser_snapshot" and result.get("recoverable")):
                            errors = True
                            return {**failed("FAILED", f"Browser tool failed: {name}; {detail['errorClass']}: {detail['message']}"),
                                    "errorDetails":detail}
                        log("[SNAPSHOT] Recoverable inspection error; continuing with a new inspection or known target")
                    actions += int(bool(result.get("executed")))
                    ids = result.get("evidenceIds") or ([result["evidenceId"]] if result.get("evidenceId") else [])
                    assertions = result.get("assertions") or ([result.get("assertion")] if ids else [])
                    for index, evidence_id in enumerate(ids):
                        item = {"id": evidence_id, "sequence": sequence,
                                "assertion": assertions[index] if index < len(assertions) else None,
                                "page": result.get("activePage")}
                        evidence[evidence_id] = item
                        log(f"[VERIFY] Browser assertion passed ({evidence_id})")
                    if not result.get("isError") and not (result.get("unchanged") or result.get("denied")):
                        log(f"[MCP] {name} successful")
                    checkpoint_required = bool(result.get("transition") or result.get("checkpointRequired"))
                    result = {k: v for k, v in result.items() if k not in {"code", "recording"}}
                else:
                    result = {"isError": True, "reason": "Tool is not available"}
                result = context.observe(name, args, secrets.object(result))
                replies.append(types.Part(function_response=types.FunctionResponse(name=name, id=function.id, response=result)))
            context.remember(content, replies)
            if sum(len(item.model_dump_json()) for item in context.request(state, evidence)) > 30000:
                return failed("BLOCKED", "Tool batch exceeded the context budget; no success inferred")
        return failed("BLOCKED", "Step tool-call limit reached")

    try:
        async with asyncio.timeout(config.step_timeout):
            return await loop()
    except TimeoutError:
        return failed("BLOCKED", "Step deadline exceeded; browser outcome may be uncertain")
    except Exception as exc:
        details = {**secrets.diagnostic(exc), "stepNumber":step.stepNumber, "activePage":state.active_page}
        log("[ERROR] " + json.dumps(details, ensure_ascii=False))
        return {**failed("BLOCKED", f"{details['errorClass']}: {details['error']}"), "errorDetails":details}


async def run_step(step, ai, browser, state, config, secrets, log):
    """Every production step plans and executes semantics before model tool choice."""
    if not any(tool["name"] == "browser_semantic_plan" for tool in browser.tools):
        return {"stepNumber":step.stepNumber,"status":"BLOCKED",
                "reason":"Semantic plan executor is unavailable", "verification":"", "evidence":[]}
    try:
        if any(tool["name"] == "poc_step_start" for tool in browser.tools):
            started = await browser.call("poc_step_start", {"stepNumber":step.stepNumber})
            for event in started.pop("logEvents", []): log(event)
            if started.get("isError"):
                raise RuntimeError(started.get("error", "Jira step page lock failed"))
            state.observe(started)
        async with asyncio.timeout(config.step_timeout):
            plan = await ai.plan_step(step,state.compact(),config.base_url,config.secret_names)
            actions = validate_plan(plan,step.expectedResult)
            if secrets.contains(json.dumps(plan,ensure_ascii=False)):
                raise ValueError("Semantic plan contains a raw credential")
            log(f"[PLAN] Step {step.stepNumber}: {len(plan['actions'])} actions, "
                f"{len(plan['expectedAssertions'])} expected assertions")
            result = await browser.call("browser_semantic_plan",{"actions":actions})
    except (ValueError, AttributeError, KeyError) as exc:
        return {"stepNumber":step.stepNumber,"status":"BLOCKED",
                "reason":"Semantic planning failed: " + secrets.diagnostic(exc)["error"],
                "verification":"", "evidence":[]}
    except TimeoutError:
        return {"stepNumber":step.stepNumber,"status":"BLOCKED",
                "reason":"Semantic planning or execution timed out", "verification":"", "evidence":[]}
    except Exception as exc:
        return {"stepNumber":step.stepNumber,"status":"BLOCKED",
                "reason":"Semantic plan could not execute: " + secrets.diagnostic(exc)["error"],
                "verification":"", "evidence":[]}
    for event in result.pop("logEvents", []): log(event)
    state.metrics["browserToolCalls"] += result.get("batchExecuted",1)
    state.observe(result)
    if result.get("resolverStatus") in {"NO_MATCH","AMBIGUOUS"}:
        log(f"[RESOLVE] {result['resolverStatus']} candidates={len(result.get('candidates') or [])}")
        return await run_fallback_step(step,ai,browser,state,config,secrets,log,
                                       prestarted=True,semantic_result=result)
    if result.get("isError"):
        detail = secrets.object({"tool":"browser_semantic_plan", "errorClass":result.get("errorClass","BrowserToolError"),
                                 "message":result.get("error","operation did not succeed"),
                                 "activePage":state.active_page,"stepNumber":step.stepNumber})
        log("[MCP] " + json.dumps(detail,ensure_ascii=False))
        return {"stepNumber":step.stepNumber,"status":"FAILED",
                "reason":f"Semantic browser execution failed: {detail['message']}",
                "verification":"","evidence":[],"errorDetails":detail}
    ids = result.get("evidenceIds") or []
    assertions = result.get("assertions") or []
    complete = result.get("executed") and not result.get("remainingActions") and not result.get("denied")
    verified = len(ids) == len(plan["expectedAssertions"])
    if not complete or not verified:
        return {"stepNumber":step.stepNumber,"status":"BLOCKED",
                "reason":"Semantic plan did not execute and verify every expected assertion",
                "verification":"","evidence":[]}
    evidence = [{"id":item_id,"sequence":1,"assertion":assertions[index] if index < len(assertions) else None,
                 "page":result.get("activePage")} for index,item_id in enumerate(ids)]
    state.completed_steps.append(step.stepNumber)
    state.summary = secrets.clean((step.expectedResult or step.action)[:400])
    return secrets.object({"stepNumber":step.stepNumber,"status":"COMPLETED",
                           "reason":"Semantic plan actions and expected assertions completed",
                           "verification":step.expectedResult,"evidence":evidence})
